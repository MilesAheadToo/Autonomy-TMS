"""
SAP Schema Discovery Agent.

Connects to a HANA DB and discovers the actual SAP table/field schema, then maps
it against the expected schema (sap_expected_schema.py) to produce a SchemaProfile.

Handles schema variations across SAP releases, industry solutions, and custom
Z-field extensions. Results are stored as a JSONB schema_profile on the
SAPConnection record so extractors can use dynamic field lists instead of
hardcoded mappings.
"""

import asyncio
import json
import logging
import os
import re
import time
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from app.models.sap_schema_profile import (
    CustomFieldDiscovery,
    EntityProfile,
    FieldMatch,
    JoinStep,
    MatchType,
    MissingField,
    SchemaProfile,
)
from app.services.sap_expected_schema import (
    EXPECTED_SCHEMA,
    get_all_required_tables,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Confidence thresholds
# ---------------------------------------------------------------------------
CONFIDENCE_AUTO_ACCEPT = 0.90
CONFIDENCE_SUGGEST = 0.70
CONFIDENCE_REJECT = 0.40

# SAP synonym map: alternative field names across releases
_FIELD_SYNONYMS: Dict[str, List[str]] = {
    "MATNR": ["MATERIAL", "PRODUCT", "MATL_NR"],
    "WERKS": ["PLANT", "PLANT_CD"],
    "LIFNR": ["VENDOR", "SUPPLIER", "VENDOR_NO"],
    "KUNNR": ["CUSTOMER", "CUST_NO"],
    "VBELN": ["SD_DOC", "DELIVERY", "SALES_DOC"],
    "EBELN": ["PO_NUMBER", "PUR_DOC"],
    "AUFNR": ["ORDER_NO", "PROD_ORD"],
    "STLNR": ["BOM_NO", "BILL_OF_MATERIAL"],
    "MBLNR": ["MAT_DOC", "MATERIAL_DOC"],
    "LGORT": ["STGE_LOC", "STOR_LOC"],
    "MEINS": ["BASE_UOM", "BASE_UNIT", "UOM"],
    "MAKTX": ["MATL_DESC", "MATERIAL_DESC", "DESCRIPTION"],
    "NAME1": ["NAME", "DESCR"],
    "BUKRS": ["COMP_CODE", "COMPANY_CODE"],
    "MENGE": ["QUANTITY", "QTY"],
    "NETPR": ["NET_PRICE", "PRICE"],
    "WAERS": ["CURRENCY", "CURR"],
    "LABST": ["UNRESTRICTED", "UNRESTR_STK", "VAL_STCK"],
    "EISBE": ["SAFETY_STK", "SAFE_STOCK"],
    "PLIFZ": ["PLAN_DEL_TM", "DELIVERY_TIME"],
}

# Reverse map: synonym -> canonical field
_SYNONYM_TO_CANONICAL: Dict[str, str] = {}
for _canonical, _syns in _FIELD_SYNONYMS.items():
    for _s in _syns:
        _SYNONYM_TO_CANONICAL[_s.upper()] = _canonical
    _SYNONYM_TO_CANONICAL[_canonical] = _canonical


class SAPSchemaAgent:
    """SAP Schema Discovery Agent.

    Discovers and maps SAP table structures at connection time.
    Handles schema variations across SAP releases, industry solutions,
    and custom extensions (Z-fields).
    """

    def __init__(
        self,
        hostname: str,
        port: int,
        user: str,
        password: str,
        schema: str = "SAPHANADB",
    ):
        self.hostname = hostname
        self.port = port
        self.user = user
        self.password = password
        self.schema = schema

        # Populated by _pull_catalog
        self._dd03l: Dict[str, List[Dict[str, Any]]] = {}   # table -> [field dicts]
        self._dd04t: Dict[str, str] = {}                      # fieldname -> description
        self._sys_columns: Dict[str, Set[str]] = {}            # table -> {columns}
        self._dd05s: Dict[str, List[Dict[str, str]]] = {}     # table -> [FK dicts]
        self._sap_release: str = ""

    # ------------------------------------------------------------------
    # HANA connection helper
    # ------------------------------------------------------------------

    def _connect(self):
        """Create a HANA connection (synchronous)."""
        from hdbcli import dbapi
        return dbapi.connect(
            address=self.hostname,
            port=self.port,
            user=self.user,
            password=self.password,
            encrypt=False,
        )

    def _execute_query(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """Execute a query and return list of dicts."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            return [dict(zip(cols, row)) for row in rows]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def discover(
        self,
        progress_callback: Optional[Callable] = None,
    ) -> SchemaProfile:
        """Full schema discovery run. Returns a SchemaProfile.

        Steps:
        1. Pull catalog from HANA (DD03L, DD04T, SYS.TABLE_COLUMNS, DD05S)
        2. Match each expected field against actual catalog
        3. Discover join paths for cross-table fields
        4. Discover Z-fields (custom extensions)
        5. Optionally validate with sample data
        6. Optionally resolve ambiguous matches with LLM
        7. Compute confidence scores and return profile
        """
        start = time.time()
        profile = SchemaProfile()

        # Step 1: Pull catalog
        if progress_callback:
            await progress_callback("pull_catalog", 0, 0)
        await asyncio.to_thread(self._pull_catalog)
        profile.sap_release = self._sap_release

        tables_scanned = len(self._sys_columns)
        fields_scanned = sum(len(cols) for cols in self._sys_columns.values())
        profile.tables_scanned = tables_scanned
        profile.fields_scanned = fields_scanned

        if progress_callback:
            await progress_callback("catalog_done", tables_scanned, fields_scanned)

        # Step 2 & 3: Match fields for each entity
        for entity_name, entity_def in EXPECTED_SCHEMA.items():
            if progress_callback:
                await progress_callback("matching", 0, 0)
            entity_profile = self._match_entity(entity_name, entity_def)
            profile.entities[entity_name] = entity_profile

        # Step 4: Discover Z-fields
        if progress_callback:
            await progress_callback("z_fields", 0, 0)
        profile.custom_fields = self._discover_z_fields()

        # Step 5: Validate with sample data for matches with 0.7-0.9 confidence
        ambiguous = self._collect_ambiguous_matches(profile)
        if ambiguous:
            if progress_callback:
                await progress_callback("sample_validation", len(ambiguous), 0)
            await asyncio.to_thread(self._validate_with_sample_data, ambiguous)

        # Step 6: LLM resolution for still-ambiguous matches
        still_ambiguous = [m for m in ambiguous if not m.user_confirmed and m.candidates]
        if still_ambiguous:
            if progress_callback:
                await progress_callback("llm_resolution", len(still_ambiguous), 0)
            await self._resolve_with_llm(still_ambiguous)

        # Step 7: Collect missing fields
        for entity_name, ep in profile.entities.items():
            for field_name, fm in ep.field_matches.items():
                if fm.match_type == MatchType.NOT_FOUND:
                    field_def = EXPECTED_SCHEMA.get(entity_name, {}).get("fields", {}).get(field_name, {})
                    profile.missing_fields.append(MissingField(
                        entity=entity_name,
                        field_name=field_name,
                        sap_field_hint=field_def.get("sap_field", ""),
                        required=field_def.get("required", False),
                        reason="Field not found in HANA catalog",
                    ))

        # Compute overall confidence
        profile.confidence_score = self._compute_overall_confidence(profile)
        profile.discovery_duration_seconds = round(time.time() - start, 2)

        logger.info(
            f"Schema discovery complete: {tables_scanned} tables, "
            f"{fields_scanned} fields scanned, "
            f"overall confidence={profile.confidence_score:.2f}, "
            f"{len(profile.missing_fields)} missing, "
            f"{len(profile.custom_fields)} Z-fields, "
            f"duration={profile.discovery_duration_seconds}s"
        )

        return profile

    # ------------------------------------------------------------------
    # Step 1: Pull HANA catalog
    # ------------------------------------------------------------------

    def _pull_catalog(self):
        """Pull DD03L, DD04T, DD05S, SYS.TABLE_COLUMNS from HANA.

        DD03L: Field definitions per table
        DD04T: Field text descriptions (English)
        DD05S: Foreign key relationships
        SYS.TABLE_COLUMNS: Actual runtime column existence
        """
        required_tables = get_all_required_tables()
        table_list = ", ".join(f"'{t}'" for t in sorted(required_tables))

        conn = self._connect()
        try:
            cur = conn.cursor()

            # ---- SAP release ----
            try:
                cur.execute(
                    f"SELECT TOP 1 \"SESSION\" FROM {self.schema}.\"CVERS\" "
                    f"WHERE \"COMPONENT\" = 'SAP_ABA'"
                )
                row = cur.fetchone()
                if row:
                    self._sap_release = str(row[0]).strip()
            except Exception:
                # CVERS may not exist or column names differ
                try:
                    cur.execute(
                        f"SELECT TOP 1 \"RELEASE\" FROM {self.schema}.\"CVERS\" "
                        f"WHERE \"COMPONENT\" = 'SAP_ABA'"
                    )
                    row = cur.fetchone()
                    if row:
                        self._sap_release = str(row[0]).strip()
                except Exception:
                    self._sap_release = "unknown"

            # ---- SYS.TABLE_COLUMNS (ground truth) ----
            # Scan both the configured schema AND common system schemas
            cur.execute(
                "SELECT TABLE_NAME, COLUMN_NAME FROM SYS.TABLE_COLUMNS "
                f"WHERE SCHEMA_NAME = '{self.schema}'"
            )
            for row in cur.fetchall():
                tbl = str(row[0]).upper()
                col = str(row[1]).upper()
                self._sys_columns.setdefault(tbl, set()).add(col)

            # ---- DD03L (field catalog) ----
            try:
                cur.execute(
                    f"SELECT \"TABNAME\", \"FIELDNAME\", \"POSITION\", \"DATATYPE\", "
                    f"\"LENG\", \"DECIMALS\", \"ROLLNAME\", \"DOMNAME\", \"CHECKTABLE\" "
                    f"FROM {self.schema}.\"DD03L\" "
                    f"WHERE \"TABNAME\" IN ({table_list}) "
                    f"AND \"FIELDNAME\" NOT LIKE '.%'"
                )
                for row in cur.fetchall():
                    tbl = str(row[0]).strip().upper()
                    entry = {
                        "FIELDNAME": str(row[1]).strip().upper(),
                        "POSITION": row[2],
                        "DATATYPE": str(row[3]).strip() if row[3] else "",
                        "LENG": row[4],
                        "DECIMALS": row[5],
                        "ROLLNAME": str(row[6]).strip() if row[6] else "",
                        "DOMNAME": str(row[7]).strip() if row[7] else "",
                        "CHECKTABLE": str(row[8]).strip() if row[8] else "",
                    }
                    self._dd03l.setdefault(tbl, []).append(entry)
            except Exception as e:
                logger.warning(f"DD03L query failed (not an ABAP schema?): {e}")
                # Fall back to SYS.TABLE_COLUMNS only

            # Also pull DD03L for Z-tables in the schema
            try:
                cur.execute(
                    f"SELECT \"TABNAME\", \"FIELDNAME\", \"POSITION\", \"DATATYPE\", "
                    f"\"LENG\", \"DECIMALS\", \"ROLLNAME\", \"DOMNAME\", \"CHECKTABLE\" "
                    f"FROM {self.schema}.\"DD03L\" "
                    f"WHERE \"TABNAME\" LIKE 'Z%' "
                    f"AND \"FIELDNAME\" NOT LIKE '.%'"
                )
                for row in cur.fetchall():
                    tbl = str(row[0]).strip().upper()
                    entry = {
                        "FIELDNAME": str(row[1]).strip().upper(),
                        "POSITION": row[2],
                        "DATATYPE": str(row[3]).strip() if row[3] else "",
                        "LENG": row[4],
                        "DECIMALS": row[5],
                        "ROLLNAME": str(row[6]).strip() if row[6] else "",
                        "DOMNAME": str(row[7]).strip() if row[7] else "",
                        "CHECKTABLE": str(row[8]).strip() if row[8] else "",
                    }
                    self._dd03l.setdefault(tbl, []).append(entry)
            except Exception:
                pass

            # ---- DD04T (field descriptions, English) ----
            try:
                cur.execute(
                    f"SELECT \"ROLLNAME\", \"DDTEXT\" "
                    f"FROM {self.schema}.\"DD04T\" "
                    f"WHERE \"DDLANGUAGE\" = 'E'"
                )
                for row in cur.fetchall():
                    rollname = str(row[0]).strip().upper()
                    text = str(row[1]).strip() if row[1] else ""
                    if text:
                        self._dd04t[rollname] = text
            except Exception as e:
                logger.warning(f"DD04T query failed: {e}")

            # ---- DD05S (foreign key relationships) ----
            try:
                cur.execute(
                    f"SELECT \"TABNAME\", \"FIELDNAME\", \"FORTABLE\", \"FORKEY\" "
                    f"FROM {self.schema}.\"DD05S\" "
                    f"WHERE \"TABNAME\" IN ({table_list}) "
                    f"OR \"FORTABLE\" IN ({table_list})"
                )
                for row in cur.fetchall():
                    tbl = str(row[0]).strip().upper()
                    entry = {
                        "FIELDNAME": str(row[1]).strip().upper(),
                        "FORTABLE": str(row[2]).strip().upper(),
                        "FORKEY": str(row[3]).strip().upper(),
                    }
                    self._dd05s.setdefault(tbl, []).append(entry)
            except Exception as e:
                logger.warning(f"DD05S query failed: {e}")

        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Step 2: Match fields per entity
    # ------------------------------------------------------------------

    def _match_entity(self, entity_name: str, entity_def: dict) -> EntityProfile:
        """Match all expected fields for one entity."""
        primary_table = entity_def["primary_table"]
        primary_exists = primary_table.upper() in self._sys_columns

        ep = EntityProfile(
            entity_name=entity_name,
            primary_table=primary_table,
            primary_table_exists=primary_exists,
        )

        fields_def = entity_def.get("fields", {})
        matched_count = 0
        required_matched = 0
        required_count = 0

        for field_name, field_def in fields_def.items():
            is_required = field_def.get("required", False)
            if is_required:
                required_count += 1

            fm = self._match_single_field(field_name, field_def, entity_def)
            ep.field_matches[field_name] = fm

            if fm.match_type != MatchType.NOT_FOUND:
                matched_count += 1
                if is_required:
                    required_matched += 1

        total = len(fields_def)
        ep.coverage_pct = round((matched_count / total * 100) if total else 0, 1)
        ep.required_coverage_pct = round(
            (required_matched / required_count * 100) if required_count else 100, 1
        )

        return ep

    def _match_single_field(
        self,
        field_name: str,
        field_def: dict,
        entity_def: dict,
    ) -> FieldMatch:
        """Find the best match for a single expected field."""
        sap_field = field_def.get("sap_field", "").upper()
        target_table = field_def.get("table", entity_def["primary_table"]).upper()
        join_field = field_def.get("join", "")
        synonyms = field_def.get("synonyms", [])
        desc_keywords = field_def.get("description_keywords", [])
        fallback_tables = field_def.get("fallback_tables", [])

        # Collect all tables to search
        tables_to_search = [target_table]
        for fb in fallback_tables:
            if fb.upper() not in tables_to_search:
                tables_to_search.append(fb.upper())

        # ---- Pass 1: Exact match in SYS.TABLE_COLUMNS ----
        for tbl in tables_to_search:
            actual_cols = self._sys_columns.get(tbl, set())
            if sap_field in actual_cols:
                join_path = self._build_join_path(
                    entity_def["primary_table"].upper(), tbl, join_field
                ) if tbl != entity_def["primary_table"].upper() else None

                return FieldMatch(
                    expected_field=field_name,
                    matched_table=tbl,
                    matched_field=sap_field,
                    confidence=1.0,
                    match_type=MatchType.EXACT,
                    join_path=join_path,
                    description=self._get_field_description(tbl, sap_field),
                )

        # ---- Pass 2: Synonym match ----
        all_synonyms = list(_FIELD_SYNONYMS.get(sap_field, []))
        all_synonyms.extend(s.upper() for s in synonyms)

        for tbl in tables_to_search:
            actual_cols = self._sys_columns.get(tbl, set())
            for syn in all_synonyms:
                if syn.upper() in actual_cols:
                    join_path = self._build_join_path(
                        entity_def["primary_table"].upper(), tbl, join_field
                    ) if tbl != entity_def["primary_table"].upper() else None

                    return FieldMatch(
                        expected_field=field_name,
                        matched_table=tbl,
                        matched_field=syn.upper(),
                        confidence=0.90,
                        match_type=MatchType.SYNONYM,
                        join_path=join_path,
                        description=self._get_field_description(tbl, syn.upper()),
                    )

        # ---- Pass 3: Fuzzy name match in DD03L ----
        best_match: Optional[FieldMatch] = None
        best_score = 0.0
        candidates: List[Dict[str, Any]] = []

        for tbl in tables_to_search:
            dd_fields = self._dd03l.get(tbl, [])
            actual_cols = self._sys_columns.get(tbl, set())

            for dd_entry in dd_fields:
                dd_field = dd_entry["FIELDNAME"]
                if dd_field not in actual_cols:
                    continue  # Skip DD03L entries that don't exist in runtime

                # Name similarity
                name_sim = SequenceMatcher(None, sap_field.lower(), dd_field.lower()).ratio()

                # Description keyword matching
                desc = self._get_field_description_from_dd(dd_entry)
                desc_sim = 0.0
                if desc and desc_keywords:
                    desc_lower = desc.lower()
                    kw_hits = sum(1 for kw in desc_keywords if kw.lower() in desc_lower)
                    desc_sim = kw_hits / len(desc_keywords) if desc_keywords else 0

                # Combined score (name weighted higher)
                score = name_sim * 0.6 + desc_sim * 0.4

                if score > 0.5:
                    candidates.append({
                        "table": tbl,
                        "field": dd_field,
                        "name_score": round(name_sim, 3),
                        "desc_score": round(desc_sim, 3),
                        "combined": round(score, 3),
                        "description": desc,
                    })

                if score > best_score:
                    best_score = score
                    join_path = self._build_join_path(
                        entity_def["primary_table"].upper(), tbl, join_field
                    ) if tbl != entity_def["primary_table"].upper() else None

                    best_match = FieldMatch(
                        expected_field=field_name,
                        matched_table=tbl,
                        matched_field=dd_field,
                        confidence=round(min(score, 0.89), 2),  # Cap below exact
                        match_type=MatchType.DESCRIPTION,
                        join_path=join_path,
                        description=desc,
                        candidates=sorted(candidates, key=lambda c: -c["combined"])[:5],
                    )

        # ---- Pass 4: Description-only match (no name similarity needed) ----
        if (not best_match or best_score < CONFIDENCE_SUGGEST) and desc_keywords:
            for tbl in tables_to_search:
                dd_fields = self._dd03l.get(tbl, [])
                actual_cols = self._sys_columns.get(tbl, set())

                for dd_entry in dd_fields:
                    dd_field = dd_entry["FIELDNAME"]
                    if dd_field not in actual_cols:
                        continue

                    desc = self._get_field_description_from_dd(dd_entry)
                    if not desc:
                        continue

                    desc_lower = desc.lower()
                    kw_hits = sum(1 for kw in desc_keywords if kw.lower() in desc_lower)
                    if kw_hits == 0:
                        continue

                    desc_score = kw_hits / len(desc_keywords)
                    if desc_score > best_score:
                        best_score = desc_score
                        join_path = self._build_join_path(
                            entity_def["primary_table"].upper(), tbl, join_field
                        ) if tbl != entity_def["primary_table"].upper() else None

                        best_match = FieldMatch(
                            expected_field=field_name,
                            matched_table=tbl,
                            matched_field=dd_field,
                            confidence=round(min(desc_score * 0.85, 0.85), 2),
                            match_type=MatchType.DESCRIPTION,
                            join_path=join_path,
                            description=desc,
                        )

        if best_match and best_score >= CONFIDENCE_REJECT:
            return best_match

        # ---- Pass 5: Check if field exists in an unexpected table via DD05S FKs ----
        fk_match = self._search_via_foreign_keys(
            sap_field, entity_def["primary_table"].upper(), desc_keywords
        )
        if fk_match:
            return fk_match

        # Not found
        return FieldMatch(
            expected_field=field_name,
            match_type=MatchType.NOT_FOUND,
            candidates=sorted(candidates, key=lambda c: -c["combined"])[:5] if candidates else None,
        )

    # ------------------------------------------------------------------
    # Join path builder
    # ------------------------------------------------------------------

    def _build_join_path(
        self, primary_table: str, target_table: str, join_field: str
    ) -> Optional[List[JoinStep]]:
        """Build a join path from primary to target table."""
        if not join_field or primary_table == target_table:
            return None

        join_field = join_field.upper()

        # Verify the join field exists in both tables
        primary_cols = self._sys_columns.get(primary_table, set())
        target_cols = self._sys_columns.get(target_table, set())

        if join_field in primary_cols and join_field in target_cols:
            return [JoinStep(
                from_table=primary_table,
                from_field=join_field,
                to_table=target_table,
                to_field=join_field,
            )]

        # Try to find a common key field using DD05S foreign keys
        fk_entries = self._dd05s.get(target_table, [])
        for fk in fk_entries:
            if fk["FORTABLE"] == primary_table:
                return [JoinStep(
                    from_table=primary_table,
                    from_field=fk["FORKEY"],
                    to_table=target_table,
                    to_field=fk["FIELDNAME"],
                )]

        # Try common field names between tables
        common = primary_cols & target_cols
        # Prefer well-known key fields
        key_priority = ["MATNR", "WERKS", "LIFNR", "KUNNR", "VBELN", "EBELN",
                        "AUFNR", "STLNR", "MBLNR"]
        for key in key_priority:
            if key in common:
                return [JoinStep(
                    from_table=primary_table,
                    from_field=key,
                    to_table=target_table,
                    to_field=key,
                )]

        if common:
            # Use first common field
            field = sorted(common)[0]
            return [JoinStep(
                from_table=primary_table,
                from_field=field,
                to_table=target_table,
                to_field=field,
            )]

        return None

    # ------------------------------------------------------------------
    # Foreign key search
    # ------------------------------------------------------------------

    def _search_via_foreign_keys(
        self, sap_field: str, primary_table: str, desc_keywords: list
    ) -> Optional[FieldMatch]:
        """Search for a field in related tables via DD05S foreign keys."""
        fk_entries = self._dd05s.get(primary_table, [])
        for fk in fk_entries:
            related_table = fk["FORTABLE"]
            related_cols = self._sys_columns.get(related_table, set())
            if sap_field in related_cols:
                join_path = [JoinStep(
                    from_table=primary_table,
                    from_field=fk["FIELDNAME"],
                    to_table=related_table,
                    to_field=fk["FORKEY"],
                )]
                return FieldMatch(
                    expected_field=sap_field,
                    matched_table=related_table,
                    matched_field=sap_field,
                    confidence=0.75,
                    match_type=MatchType.JOIN_PATH,
                    join_path=join_path,
                    description=self._get_field_description(related_table, sap_field),
                )
        return None

    # ------------------------------------------------------------------
    # Z-field discovery
    # ------------------------------------------------------------------

    def _discover_z_fields(self) -> List[CustomFieldDiscovery]:
        """Discover Z-fields and Z-tables that could extend standard mappings."""
        z_fields: List[CustomFieldDiscovery] = []
        required_tables = get_all_required_tables()

        # Z-fields in standard tables
        for tbl in required_tables:
            actual_cols = self._sys_columns.get(tbl.upper(), set())
            for col in sorted(actual_cols):
                if col.startswith("Z") or col.startswith("ZZ"):
                    desc = self._get_field_description(tbl.upper(), col)
                    dd_entry = self._find_dd_entry(tbl.upper(), col)
                    z_fields.append(CustomFieldDiscovery(
                        table_name=tbl.upper(),
                        field_name=col,
                        data_type=dd_entry.get("DATATYPE", "") if dd_entry else "",
                        length=dd_entry.get("LENG", 0) if dd_entry else 0,
                        description=desc,
                    ))

        # Z-tables (tables starting with Z)
        for tbl, cols in self._sys_columns.items():
            if tbl.startswith("Z"):
                for col in sorted(cols):
                    dd_entry = self._find_dd_entry(tbl, col)
                    desc = self._get_field_description(tbl, col)
                    # Try to suggest AWS SC mapping based on field name
                    suggested = self._suggest_z_field_mapping(col, desc)
                    z_fields.append(CustomFieldDiscovery(
                        table_name=tbl,
                        field_name=col,
                        data_type=dd_entry.get("DATATYPE", "") if dd_entry else "",
                        length=dd_entry.get("LENG", 0) if dd_entry else 0,
                        description=desc,
                        suggested_entity=suggested[0] if suggested else None,
                        suggested_aws_sc_field=suggested[1] if suggested else None,
                        confidence=suggested[2] if suggested else 0.0,
                    ))

        return z_fields

    def _suggest_z_field_mapping(
        self, field_name: str, description: str
    ) -> Optional[Tuple[str, str, float]]:
        """Try to suggest an AWS SC DM entity/field for a Z-field."""
        # Check if the field name maps to a known canonical SAP field
        canonical = _SYNONYM_TO_CANONICAL.get(field_name.upper())
        if canonical:
            # Search expected schema for this canonical field
            for entity_name, entity_def in EXPECTED_SCHEMA.items():
                for aws_field, field_def in entity_def["fields"].items():
                    if field_def.get("sap_field", "").upper() == canonical:
                        return (entity_name, aws_field, 0.60)

        # Keyword match on description
        if description:
            desc_lower = description.lower()
            for entity_name, entity_def in EXPECTED_SCHEMA.items():
                for aws_field, field_def in entity_def["fields"].items():
                    for kw in field_def.get("description_keywords", []):
                        if kw.lower() in desc_lower:
                            return (entity_name, aws_field, 0.40)

        return None

    # ------------------------------------------------------------------
    # Sample data validation
    # ------------------------------------------------------------------

    def _validate_with_sample_data(self, matches: List[FieldMatch]):
        """Query sample rows to validate field matches contain expected data."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            for fm in matches:
                if not fm.matched_table or not fm.matched_field:
                    continue
                try:
                    cur.execute(
                        f'SELECT DISTINCT "{fm.matched_field}" '
                        f'FROM {self.schema}."{fm.matched_table}" '
                        f'WHERE "{fm.matched_field}" IS NOT NULL '
                        f'LIMIT 10'
                    )
                    rows = cur.fetchall()
                    fm.sample_values = [str(r[0]).strip() for r in rows if r[0] is not None]

                    # Validate against expected_values if available
                    field_def = self._find_field_def_for_match(fm)
                    if field_def and field_def.get("expected_values") and fm.sample_values:
                        expected = set(str(v) for v in field_def["expected_values"])
                        actual = set(fm.sample_values)
                        overlap = expected & actual
                        if overlap:
                            # Boost confidence
                            fm.confidence = min(fm.confidence + 0.10, 0.95)
                            fm.match_type = MatchType.SAMPLE_VALIDATED
                        elif fm.confidence < CONFIDENCE_AUTO_ACCEPT:
                            # Penalize slightly
                            fm.confidence = max(fm.confidence - 0.05, 0.0)

                except Exception as e:
                    logger.debug(f"Sample validation failed for {fm.matched_table}.{fm.matched_field}: {e}")
        finally:
            conn.close()

    def _find_field_def_for_match(self, fm: FieldMatch) -> Optional[dict]:
        """Find the expected schema field definition for a FieldMatch."""
        for entity_def in EXPECTED_SCHEMA.values():
            for field_name, field_def in entity_def.get("fields", {}).items():
                if field_name == fm.expected_field:
                    return field_def
        return None

    # ------------------------------------------------------------------
    # LLM resolution for ambiguous matches
    # ------------------------------------------------------------------

    async def _resolve_with_llm(self, ambiguous_matches: List[FieldMatch]):
        """Use LLM to resolve ambiguous field matches.

        Routes through Core's ``azirella_assistant.OpenAICompatibleClient``
        substrate (§3.16) — direct HTTP calls to the OpenAI-compat
        chat endpoint are not allowed outside the substrate.
        """
        try:
            from azirella_assistant import (
                ChatMessage,
                OpenAICompatibleClient,
                Workload,
            )
        except ImportError:
            logger.debug(
                "azirella_assistant not available for LLM resolution, skipping"
            )
            return

        client = OpenAICompatibleClient(workload=Workload.CHAT)
        for fm in ambiguous_matches:
            if not fm.candidates or len(fm.candidates) < 2:
                continue

            prompt = self._build_llm_prompt(fm)
            try:
                resp = await client.complete(
                    messages=[
                        ChatMessage(role="system", content=(
                            "You are an SAP technical consultant. Given an expected field "
                            "and multiple candidate SAP fields, determine which candidate "
                            "is the best match. Reply with ONLY the field name of the best "
                            "match, nothing else."
                        )),
                        ChatMessage(role="user", content=prompt),
                    ],
                    temperature=0.1,
                    max_tokens=50,
                )
                answer = (resp.content or "").strip().upper()
                # Remove <think> tags if present (qwen3)
                answer = re.sub(
                    r"<think>.*?</think>", "", answer, flags=re.DOTALL
                ).strip().upper()

                # Find the matching candidate
                for cand in fm.candidates:
                    if cand["field"].upper() == answer:
                        fm.matched_table = cand["table"]
                        fm.matched_field = cand["field"]
                        fm.confidence = min(cand["combined"] + 0.10, 0.92)
                        fm.match_type = MatchType.LLM_RESOLVED
                        fm.description = cand.get("description", "")
                        break

            except Exception as e:
                logger.debug(
                    f"LLM resolution failed for {fm.expected_field}: {e}"
                )

    def _build_llm_prompt(self, fm: FieldMatch) -> str:
        """Build a prompt for LLM field resolution."""
        lines = [
            f"Expected field: {fm.expected_field}",
            f"Expected SAP field hint: {fm.matched_field}",
            "",
            "Candidate SAP fields:",
        ]
        for i, cand in enumerate(fm.candidates[:5], 1):
            lines.append(
                f"  {i}. {cand['table']}.{cand['field']} "
                f"(name_score={cand['name_score']}, desc_score={cand['desc_score']}) "
                f"— {cand.get('description', 'no description')}"
            )
        lines.append("")
        lines.append("Which field is the best match? Reply with ONLY the field name.")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _get_field_description(self, table: str, field: str) -> str:
        """Get the English description for a field from DD04T via DD03L ROLLNAME."""
        dd_entry = self._find_dd_entry(table, field)
        if dd_entry:
            return self._get_field_description_from_dd(dd_entry)
        return ""

    def _get_field_description_from_dd(self, dd_entry: dict) -> str:
        """Get description text from a DD03L entry using ROLLNAME -> DD04T lookup."""
        rollname = dd_entry.get("ROLLNAME", "")
        if rollname:
            desc = self._dd04t.get(rollname.upper(), "")
            if desc:
                return desc
        # Try DOMNAME
        domname = dd_entry.get("DOMNAME", "")
        if domname:
            desc = self._dd04t.get(domname.upper(), "")
            if desc:
                return desc
        return ""

    def _find_dd_entry(self, table: str, field: str) -> Optional[dict]:
        """Find a DD03L entry for a table.field."""
        for entry in self._dd03l.get(table.upper(), []):
            if entry["FIELDNAME"] == field.upper():
                return entry
        return None

    def _collect_ambiguous_matches(self, profile: SchemaProfile) -> List[FieldMatch]:
        """Collect matches that could benefit from sample validation or LLM."""
        ambiguous = []
        for ep in profile.entities.values():
            for fm in ep.field_matches.values():
                if (
                    fm.match_type not in (MatchType.NOT_FOUND, MatchType.EXACT)
                    and CONFIDENCE_SUGGEST <= fm.confidence < CONFIDENCE_AUTO_ACCEPT
                ):
                    ambiguous.append(fm)
        return ambiguous

    def _compute_overall_confidence(self, profile: SchemaProfile) -> float:
        """Compute weighted overall confidence score."""
        total_weight = 0.0
        total_score = 0.0

        for entity_name, ep in profile.entities.items():
            for field_name, fm in ep.field_matches.items():
                field_def = EXPECTED_SCHEMA.get(entity_name, {}).get("fields", {}).get(field_name, {})
                weight = 2.0 if field_def.get("required") else 1.0
                total_weight += weight
                total_score += fm.confidence * weight

        return round(total_score / total_weight, 3) if total_weight else 0.0


# ======================================================================
# Convenience function for endpoint use
# ======================================================================

async def run_schema_discovery(
    hostname: str,
    port: int,
    user: str,
    password: str,
    schema: str = "SAPHANADB",
    progress_callback: Optional[Callable] = None,
) -> SchemaProfile:
    """Run the SAP Schema Discovery Agent and return a SchemaProfile.

    This is the main entry point for the API endpoint.
    """
    agent = SAPSchemaAgent(
        hostname=hostname,
        port=port,
        user=user,
        password=password,
        schema=schema,
    )
    return await agent.discover(progress_callback=progress_callback)
