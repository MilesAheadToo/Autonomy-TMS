import React, { useState, useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import { useDisplayPreferences } from '../../contexts/DisplayPreferencesContext';
import { useActiveConfig } from '../../contexts/ActiveConfigContext';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Button,
  Alert,
  Badge,
  Input,
  Label,
  Progress,
  Modal,
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
  Textarea,
} from '../../components/common';
import { Switch } from '../../components/ui/switch';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '../../components/ui/tooltip';
import {
  RefreshCw,
  ArrowLeftRight,
  TrendingUp,
  TrendingDown,
  Pencil,
  Save,
  X,
  Undo,
  ShieldCheck,
  GitBranch,
} from 'lucide-react';
import {
  ComposedChart, LineChart, Line, Area, XAxis, YAxis, CartesianGrid,
  Tooltip as RechartsTooltip, Legend, ResponsiveContainer,
} from 'recharts';
import { api } from '../../services/api';
import LevelPeggingGantt from '../../components/planning/LevelPeggingGantt';

const DemandPlanView = () => {
  const location = useLocation();
  const { formatProduct, formatSite } = useDisplayPreferences();
  // Pegging Gantt state
  const [peggingTarget, setPeggingTarget] = useState(null);
  const { effectiveConfigId } = useActiveConfig();
  const filtersApplied = useRef(false);
  const [forecasts, setForecasts] = useState([]);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(false);
  const [productFilter, setProductFilter] = useState('');
  const [siteFilter, setSiteFilter] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');

  // Hierarchy filters (dynamic from DAG)
  const [dimensions, setDimensions] = useState(null);
  const [timeBucket, setTimeBucket] = useState('week');
  const [productNodeId, setProductNodeId] = useState(''); // product_hierarchy_node ID
  const [categoryFilter, setCategoryFilter] = useState(''); // legacy fallback
  const [familyFilter, setFamilyFilter] = useState(''); // legacy fallback
  const [geoFilter, setGeoFilter] = useState('');  // geography node ID
  const [aggData, setAggData] = useState(null);

  // Hydrate filters from Talk To Me query routing
  useEffect(() => {
    const filters = location.state?.filters;
    if (filters && !filtersApplied.current) {
      filtersApplied.current = true;
      if (filters.product) setProductFilter(filters.product);
      if (filters.site) setSiteFilter(filters.site);
      if (filters.start_date) setStartDate(filters.start_date);
      if (filters.end_date) setEndDate(filters.end_date);
      window.history.replaceState({}, '');
    }
  }, [location.state]);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);
  const [compareDialogOpen, setCompareDialogOpen] = useState(false);
  const [deltaData, setDeltaData] = useState([]);
  const [chartData, setChartData] = useState([]);

  // Edit mode state
  const [editMode, setEditMode] = useState(false);
  const [editedForecasts, setEditedForecasts] = useState({});
  const [saving, setSaving] = useState(false);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [selectedForecastForEdit, setSelectedForecastForEdit] = useState(null);
  const [editValues, setEditValues] = useState({ p10: 0, p50: 0, p90: 0, reason: '' });

  // Fetch demand plan summary
  const fetchSummary = async () => {
    try {
      const params = {};
      if (effectiveConfigId) params.config_id = effectiveConfigId;
      if (productNodeId) params.product_node_id = productNodeId;
      else if (categoryFilter) params.category = categoryFilter;
      else if (familyFilter) params.family = familyFilter;
      if (geoFilter) params.geo_id = geoFilter;
      const response = await api.get('/demand-plan/summary', { params });
      setSummary(response.data);
    } catch (error) {
      console.error('Failed to fetch summary:', error);
    }
  };

  // Fetch current demand plan (respects hierarchy filters)
  const fetchDemandPlan = async () => {
    setLoading(true);
    setError(null);
    try {
      const params = {
        product_id: productFilter || undefined,
        product_node_id: productNodeId || undefined,
        category: !productNodeId ? (categoryFilter || undefined) : undefined,
        family: !productNodeId ? (familyFilter || undefined) : undefined,
        geo_id: geoFilter || undefined,
        start_date: startDate || undefined,
        end_date: endDate || undefined,
        limit: 1000,
      };
      if (effectiveConfigId) params.config_id = effectiveConfigId;
      const response = await api.get('/demand-plan/current', { params });
      setForecasts(response.data);

      // Prepare chart data (aggregate by date)
      const chartMap = new Map();
      response.data.forEach(f => {
        const date = new Date(f.forecast_date).toISOString().split('T')[0];
        if (!chartMap.has(date)) {
          chartMap.set(date, { date, p10: 0, p50: 0, median: 0, p90: 0 });
        }
        const entry = chartMap.get(date);
        entry.p10 += f.forecast_p10 || 0;
        entry.p50 += f.forecast_p50 || 0;
        entry.median += (f.forecast_median ?? f.forecast_p50 ?? 0);
        entry.p90 += f.forecast_p90 || 0;
      });
      setChartData(Array.from(chartMap.values()).sort((a, b) => a.date.localeCompare(b.date)));

      setSuccess(`Loaded ${response.data.length} forecasts`);
    } catch (error) {
      console.error('Failed to fetch demand plan:', error);
      setError('Failed to load demand plan. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  // Compare versions (delta analysis)
  const comparePlanVersions = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await api.get('/demand-plan/delta', {
        params: {
          version1: 'previous',
          version2: 'current',
          min_delta_pct: 5.0, // Only show changes > 5%
        }
      });
      setDeltaData(response.data);
      setCompareDialogOpen(true);
    } catch (error) {
      console.error('Failed to compare versions:', error);
      setError('Failed to compare plan versions.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSummary();
    fetchDemandPlan();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Re-fetch table+summary when hierarchy filters change
  useEffect(() => {
    if (!effectiveConfigId) return;
    fetchDemandPlan();
    fetchSummary();
  }, [productNodeId, categoryFilter, familyFilter, geoFilter]); // eslint-disable-line react-hooks/exhaustive-deps

  // Open edit dialog for a forecast
  const handleEditClick = (forecast, index) => {
    setSelectedForecastForEdit({ ...forecast, index });
    setEditValues({
      p10: forecast.forecast_p10 || 0,
      p50: forecast.forecast_p50 || 0,
      p90: forecast.forecast_p90 || 0,
      reason: '',
    });
    setEditDialogOpen(true);
  };

  // Save edit for a single forecast
  const handleSaveEdit = () => {
    if (!selectedForecastForEdit) return;

    const key = `${selectedForecastForEdit.product_id}-${selectedForecastForEdit.site_id}-${selectedForecastForEdit.forecast_date}`;
    setEditedForecasts(prev => ({
      ...prev,
      [key]: {
        ...selectedForecastForEdit,
        forecast_p10: editValues.p10,
        forecast_p50: editValues.p50,
        forecast_median: editValues.p50,
        forecast_p90: editValues.p90,
        edit_reason: editValues.reason,
        original: {
          p10: selectedForecastForEdit.forecast_p10,
          p50: selectedForecastForEdit.forecast_p50,
          p90: selectedForecastForEdit.forecast_p90,
        },
      },
    }));

    // Update local forecasts display
    setForecasts(prev => prev.map((f, i) =>
      i === selectedForecastForEdit.index
        ? { ...f, forecast_p10: editValues.p10, forecast_p50: editValues.p50, forecast_p90: editValues.p90, edited: true }
        : f
    ));

    setEditDialogOpen(false);
    setSelectedForecastForEdit(null);
  };

  // Undo a single edit
  const handleUndoEdit = (forecast, index) => {
    const key = `${forecast.product_id}-${forecast.site_id}-${forecast.forecast_date}`;
    const original = editedForecasts[key]?.original;
    if (original) {
      setForecasts(prev => prev.map((f, i) =>
        i === index
          ? { ...f, forecast_p10: original.p10, forecast_p50: original.p50, forecast_p90: original.p90, edited: false }
          : f
      ));
      setEditedForecasts(prev => {
        const { [key]: _, ...rest } = prev;
        return rest;
      });
    }
  };

  // Save all edits
  const handleSaveAllEdits = async () => {
    const edits = Object.values(editedForecasts);
    if (edits.length === 0) {
      setError('No changes to save');
      return;
    }

    setSaving(true);
    setError(null);
    try {
      await api.post('/demand-plan/override', {
        overrides: edits.map(e => ({
          product_id: e.product_id,
          site_id: e.site_id,
          forecast_date: e.forecast_date,
          forecast_p10: e.forecast_p10,
          forecast_p50: e.forecast_p50,
          forecast_median: e.forecast_median ?? e.forecast_p50,
          forecast_p90: e.forecast_p90,
          reason: e.edit_reason,
        })),
      });
      setSuccess(`Saved ${edits.length} forecast overrides`);
      setEditedForecasts({});
      setEditMode(false);
      fetchDemandPlan(); // Refresh
    } catch (error) {
      console.error('Failed to save edits:', error);
      setError('Failed to save forecast overrides');
    } finally {
      setSaving(false);
    }
  };

  // Cancel all edits
  const handleCancelEdits = () => {
    setEditedForecasts({});
    setEditMode(false);
    fetchDemandPlan(); // Reload original data
  };

  // Load hierarchy dimensions from DAG
  useEffect(() => {
    if (!effectiveConfigId) return;
    api.get('/demand-plan/hierarchy-dimensions', { params: { config_id: effectiveConfigId } })
      .then(res => {
        setDimensions(res.data);
        // Auto-select root product node if not already set
        if (!productNodeId && res.data?.product_tree?.length > 0) {
          const root = res.data.product_tree.find(n => !n.parent_id);
          if (root) setProductNodeId(String(root.id));
        }
        // Auto-select root geography node if not already set
        if (!geoFilter && res.data?.geography?.length > 0) {
          const root = res.data.geography.find(g => !g.parent_id);
          if (root) setGeoFilter(root.id);
        }
      })
      .catch(() => setDimensions(null));
  }, [effectiveConfigId]);

  // Load aggregated forecast with hierarchy filters
  useEffect(() => {
    const cfgId = effectiveConfigId;
    if (!cfgId) return;
    const params = {
      config_id: cfgId,
      time_bucket: timeBucket,
      product_node_id: productNodeId || undefined,
      category: !productNodeId ? (categoryFilter || undefined) : undefined,
      family: !productNodeId ? (familyFilter || undefined) : undefined,
      product_id: productFilter || undefined,
      geo_id: geoFilter || undefined,
      start_date: startDate || undefined,
      end_date: endDate || undefined,
    };
    api.get('/demand-plan/aggregated', { params })
      .then(res => setAggData(res.data))
      .catch(() => setAggData(null));
  }, [effectiveConfigId, timeBucket, productNodeId, categoryFilter, familyFilter, productFilter, geoFilter, siteFilter, startDate, endDate]);

  const formatNumber = (num) => {
    if (!num) return '0';
    return num.toLocaleString(undefined, { maximumFractionDigits: 2 });
  };

  const editCount = Object.keys(editedForecasts).length;

  return (
    <div className="p-6">
      <div className="flex justify-between items-start mb-4">
        <div>
          <h1 className="text-2xl font-bold">
            Demand Plan {editMode ? 'Editing' : 'Viewing'}
          </h1>
          <p className="text-sm text-muted-foreground">
            {editMode
              ? 'Click on any forecast row to edit P10/P50/P90 values'
              : 'View demand forecasts with confidence intervals (P10/P50/P90)'}
          </p>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <Switch
              checked={editMode}
              onCheckedChange={(checked) => {
                if (!checked && editCount > 0) {
                  if (window.confirm('You have unsaved changes. Discard them?')) {
                    handleCancelEdits();
                  }
                } else {
                  setEditMode(checked);
                }
              }}
              id="edit-mode"
            />
            <Label htmlFor="edit-mode" className="flex items-center gap-1">
              <Pencil className="h-4 w-4" />
              Edit Mode
            </Label>
          </div>
          {editMode && editCount > 0 && (
            <>
              <div className="relative">
                <Button
                  onClick={handleSaveAllEdits}
                  disabled={saving}
                  leftIcon={<Save className="h-4 w-4" />}
                >
                  {saving ? 'Saving...' : 'Save Changes'}
                </Button>
                <Badge variant="default" className="absolute -top-2 -right-2 h-5 w-5 flex items-center justify-center p-0 text-xs">
                  {editCount}
                </Badge>
              </div>
              <Button
                variant="outline"
                onClick={handleCancelEdits}
                disabled={saving}
                leftIcon={<X className="h-4 w-4" />}
              >
                Discard
              </Button>
            </>
          )}
        </div>
      </div>

      {/* Success/Error Messages */}
      {error && (
        <Alert variant="error" className="mb-4" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {success && (
        <Alert variant="success" className="mb-4" onClose={() => setSuccess(null)}>
          {success}
        </Alert>
      )}

      {/* Hierarchy Filters — dynamic from DAG */}
      {dimensions && (
        <Card className="mb-4">
          <CardContent className="pt-4">
            <div className="flex flex-wrap items-end gap-3">
              <div>
                <label className="text-xs font-medium text-muted-foreground block mb-1">Time Bucket</label>
                <select className="border rounded px-2 py-1.5 text-sm w-24"
                  value={timeBucket} onChange={e => setTimeBucket(e.target.value)}>
                  <option value="day">Day</option>
                  <option value="week">Week</option>
                  <option value="month">Month</option>
                </select>
              </div>
              {/* Product hierarchy drilldown (AWS SC DM product_hierarchy_node tree) */}
              {dimensions.product_tree?.length > 0 && (() => {
                const tree = dimensions.product_tree;
                const childrenOf = (parentId) => tree.filter(n => n.parent_id === parentId);
                const findNode = (id) => tree.find(n => n.id === id);

                // Build breadcrumb
                const breadcrumb = [];
                let cur = productNodeId ? findNode(parseInt(productNodeId)) : null;
                while (cur) {
                  breadcrumb.unshift(cur);
                  cur = cur.parent_id ? findNode(cur.parent_id) : null;
                }

                const children = productNodeId
                  ? childrenOf(parseInt(productNodeId))
                  : tree.filter(n => !n.parent_id);

                return (
                  <div>
                    <label className="text-xs font-medium text-muted-foreground block mb-1">Product</label>
                    {breadcrumb.length > 0 && (
                      <div className="flex items-center gap-1 mb-1 text-xs">
                        <button className="text-primary hover:underline"
                          onClick={() => { setProductNodeId(''); setCategoryFilter(''); setFamilyFilter(''); setProductFilter(''); }}>
                          All
                        </button>
                        {breadcrumb.map((n, i) => (
                          <span key={n.id} className="flex items-center gap-1">
                            <span className="text-muted-foreground">/</span>
                            <button
                              className={i === breadcrumb.length - 1 ? 'font-semibold' : 'text-primary hover:underline'}
                              onClick={() => { setProductNodeId(String(n.id)); setProductFilter(''); }}>
                              {n.name}
                            </button>
                          </span>
                        ))}
                      </div>
                    )}
                    {children.length > 0 && (
                      <select className="border rounded px-2 py-1.5 text-sm w-52" value=""
                        onChange={e => { if (e.target.value) { setProductNodeId(e.target.value); setProductFilter(''); } }}>
                        <option value="">
                          {productNodeId ? `Drill into ${findNode(parseInt(productNodeId))?.name || ''}...` : 'Select product group...'}
                        </option>
                        {children.map(n => <option key={n.id} value={n.id}>{n.name}</option>)}
                      </select>
                    )}
                  </div>
                );
              })()}
              {/* Geography drilldown (AWS SC DM geography table) */}
              {dimensions.geography?.length > 0 && (() => {
                const childrenOf = (parentId) => dimensions.geography.filter(g => g.parent_id === parentId);
                const findGeo = (id) => dimensions.geography.find(g => g.id === id);

                // Build breadcrumb path from current selection
                const breadcrumb = [];
                let current = geoFilter ? findGeo(geoFilter) : null;
                while (current) {
                  breadcrumb.unshift(current);
                  current = current.parent_id ? findGeo(current.parent_id) : null;
                }

                // Children of current selection (or top-level if none selected)
                const children = geoFilter ? childrenOf(geoFilter) : dimensions.geography.filter(g => !g.parent_id);

                return (
                  <div>
                    <label className="text-xs font-medium text-muted-foreground block mb-1">Geography</label>
                    {/* Breadcrumb */}
                    {breadcrumb.length > 0 && (
                      <div className="flex items-center gap-1 mb-1 text-xs">
                        <button className="text-primary hover:underline" onClick={() => { setGeoFilter(''); setSiteFilter(''); }}>
                          All
                        </button>
                        {breadcrumb.map((g, i) => (
                          <span key={g.id} className="flex items-center gap-1">
                            <span className="text-muted-foreground">/</span>
                            <button
                              className={i === breadcrumb.length - 1 ? 'font-semibold' : 'text-primary hover:underline'}
                              onClick={() => { setGeoFilter(g.id); setSiteFilter(''); }}
                            >
                              {g.name}
                            </button>
                          </span>
                        ))}
                      </div>
                    )}
                    {/* Child selector */}
                    {children.length > 0 && (
                      <select className="border rounded px-2 py-1.5 text-sm w-52"
                        value=""
                        onChange={e => { if (e.target.value) { setGeoFilter(e.target.value); setSiteFilter(''); } }}>
                        <option value="">
                          {geoFilter ? `Drill into ${findGeo(geoFilter)?.name || ''}...` : 'Select region...'}
                        </option>
                        {children.map(g => (
                          <option key={g.id} value={g.id}>{g.name}</option>
                        ))}
                      </select>
                    )}
                  </div>
                );
              })()}
              {/* Sites dropdown removed — geography drilldown handles site selection */}
              {(productNodeId || categoryFilter || familyFilter || geoFilter) && (
                <Button variant="ghost" size="sm" onClick={() => {
                  setProductNodeId(''); setCategoryFilter(''); setFamilyFilter(''); setGeoFilter(''); setProductFilter('');
                }}>
                  Clear Filters
                </Button>
              )}
              {aggData?.summary && (
                <div className="ml-auto text-xs text-muted-foreground">
                  {aggData.summary.total_products} products × {aggData.summary.total_sites} sites
                  {' '}| {aggData.summary.total_records.toLocaleString()} records
                  {' '}| Bucket: {timeBucket}
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Aggregated Chart (from hierarchy filters) */}
      {aggData?.series?.length > 0 && (
        <Card className="mb-4">
          <CardContent className="pt-4">
            <h3 className="text-sm font-semibold mb-2">
              Demand Forecast
              {productNodeId && dimensions?.product_tree && (() => {
                const node = dimensions.product_tree.find(n => n.id === parseInt(productNodeId));
                return node ? ` — ${node.name}` : '';
              })()}
              {!productNodeId && categoryFilter && ` — ${categoryFilter}`}
              {geoFilter && ` @ ${dimensions?.geography?.find(g => g.id === geoFilter)?.name || geoFilter}`}
            </h3>
            <ResponsiveContainer width="100%" height={350}>
              <ComposedChart data={aggData.series}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 10 }} />
                <RechartsTooltip
                  contentStyle={{ fontSize: 11, borderRadius: 6 }}
                  formatter={(value, name) => [value?.toLocaleString() ?? '—', name]}
                />
                <Legend wrapperStyle={{ fontSize: 10 }} />
                {/* Confidence band: P10-P90 shaded area */}
                <Area
                  type="monotone" dataKey="p90" stroke="none"
                  fill="#8884d8" fillOpacity={0.12} name="P90 (High)"
                  dot={false} activeDot={false}
                />
                <Area
                  type="monotone" dataKey="p10" stroke="none"
                  fill="#ffffff" fillOpacity={1} name="P10 (Low)"
                  dot={false} activeDot={false}
                />
                {/* Median forecast line */}
                <Line
                  type="monotone" dataKey="p50" stroke="#6366f1"
                  strokeWidth={2.5} name="Forecast (Median)" dot={false}
                />
                {/* P10/P90 boundary lines */}
                <Line
                  type="monotone" dataKey="p10" stroke="#86efac"
                  strokeWidth={1} strokeDasharray="4 3" name="P10 (Low)"
                  dot={false}
                />
                <Line
                  type="monotone" dataKey="p90" stroke="#fbbf24"
                  strokeWidth={1} strokeDasharray="4 3" name="P90 (High)"
                  dot={false}
                />
                {/* Actuals overlay */}
                <Line
                  type="monotone" dataKey="actual" stroke="#ef4444"
                  strokeWidth={2} name="Actual Demand"
                  dot={{ r: 2, fill: '#ef4444' }}
                  connectNulls={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      )}

      {/* Summary Cards */}
      {summary && (
        <div className="grid grid-cols-1 md:grid-cols-5 gap-4 mb-6">
          <Card>
            <CardContent className="pt-4">
              <p className="text-sm text-muted-foreground">Forecast Records</p>
              <p className="text-3xl font-bold">{formatNumber(summary.total_forecasts)}</p>
              <p className="text-xs text-muted-foreground">{summary.period_count || '—'} periods</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-4">
              <p className="text-sm text-muted-foreground">Products</p>
              <p className="text-3xl font-bold">{summary.product_count}</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-4">
              <p className="text-sm text-muted-foreground">Sites</p>
              <p className="text-3xl font-bold">{summary.site_count}</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-4">
              <p className="text-sm text-muted-foreground">Avg Demand / Period (units)</p>
              <p className="text-2xl font-bold">{formatNumber(summary.avg_demand_p50)}</p>
              <p className="text-xs text-muted-foreground">
                Median: {formatNumber(summary.avg_demand_median ?? summary.avg_demand_p50)}
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-4">
              <p className="text-sm text-muted-foreground">Date Range</p>
              <p className="text-sm font-bold">
                {summary.start_date ? new Date(summary.start_date).toLocaleDateString() : '—'}
              </p>
              <p className="text-xs text-muted-foreground">
                to {summary.end_date ? new Date(summary.end_date).toLocaleDateString() : '—'}
              </p>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Filters */}
      <Card className="mb-6">
        <CardContent className="pt-4">
          <div className="grid grid-cols-1 md:grid-cols-6 gap-4 items-end">
            <div>
              <Label>Product</Label>
              <select className="w-full border rounded px-2 py-1.5 text-sm mt-1"
                value={productFilter}
                onChange={(e) => setProductFilter(e.target.value)}>
                <option value="">All Products</option>
                {(dimensions?.forecast_products || [])
                  .filter(p => {
                    // Respect product hierarchy filter: if productNodeId is set,
                    // only show products under that node
                    if (!productNodeId || !dimensions?.product_tree) return true;
                    const nodeId = parseInt(productNodeId);
                    // Find all leaf product_ids under this node
                    const tree = dimensions.product_tree;
                    const collectIds = (parentId) => {
                      const ids = [];
                      const node = tree.find(n => n.id === parentId);
                      if (node?.product_id) ids.push(node.product_id);
                      tree.filter(n => n.parent_id === parentId).forEach(child => {
                        ids.push(...collectIds(child.id));
                      });
                      return ids;
                    };
                    const allowedIds = collectIds(nodeId);
                    return allowedIds.length === 0 || allowedIds.includes(p.id);
                  })
                  .map(p => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))
                }
              </select>
            </div>
            <div>
              <Label>Site</Label>
              <select className="w-full border rounded px-2 py-1.5 text-sm mt-1"
                value={siteFilter}
                onChange={(e) => setSiteFilter(e.target.value)}>
                <option value="">All Sites</option>
                {(dimensions?.forecast_sites || [])
                  .filter(s => {
                    // Respect geo filter: only show sites within selected geography
                    if (!geoFilter || !dimensions?.geography) return true;
                    const geoTree = dimensions.geography;
                    const collectGeoIds = (parentId) => {
                      const ids = [parentId];
                      geoTree.filter(g => g.parent_id === parentId).forEach(child => {
                        ids.push(...collectGeoIds(child.id));
                      });
                      return ids;
                    };
                    const allowedGeoIds = collectGeoIds(geoFilter);
                    return allowedGeoIds.includes(s.geo_id);
                  })
                  .map(s => (
                    <option key={s.id} value={s.id}>{s.name} ({s.type})</option>
                  ))
                }
              </select>
            </div>
            <div>
              <Label>Start Date</Label>
              <Input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="mt-1"
              />
            </div>
            <div>
              <Label>End Date</Label>
              <Input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                className="mt-1"
              />
            </div>
            <Button
              onClick={fetchDemandPlan}
              disabled={loading}
              leftIcon={<RefreshCw className="h-4 w-4" />}
            >
              Refresh
            </Button>
            <Button
              variant="outline"
              onClick={comparePlanVersions}
              disabled={loading}
              leftIcon={<ArrowLeftRight className="h-4 w-4" />}
            >
              Compare
            </Button>
          </div>
        </CardContent>
      </Card>

      {loading && <Progress className="mb-4" />}

      {/* Chart is now the aggregated chart above (responds to hierarchy filters) */}

      {/* Forecast Table */}
      <Card>
        <CardHeader>
          <CardTitle>Forecast Details</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Product ID</TableHead>
                <TableHead>Site ID</TableHead>
                <TableHead>Forecast Date</TableHead>
                <TableHead className="text-right">P10 (Low)</TableHead>
                <TableHead className="text-right">Median (Most Likely)</TableHead>
                <TableHead className="text-right">Forecast Median</TableHead>
                <TableHead className="text-right">P90 (High)</TableHead>
                <TableHead className="text-center">Confidence</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Source</TableHead>
                <TableHead className="text-center">Pegging</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {forecasts.length === 0 && !loading && (
                <TableRow>
                  <TableCell colSpan={10} className="text-center py-8">
                    <p className="text-muted-foreground">
                      No forecasts found. Adjust filters and try again.
                    </p>
                  </TableCell>
                </TableRow>
              )}
              {forecasts.slice(0, 100).map((forecast, index) => (
                <TableRow
                  key={index}
                  className={`${editMode ? 'cursor-pointer hover:bg-muted/50' : ''} ${forecast.edited ? 'bg-amber-50 dark:bg-amber-950/20' : ''}`}
                  onClick={() => editMode && handleEditClick(forecast, index)}
                >
                  <TableCell>{formatProduct(forecast.product_id, forecast.product_name)}</TableCell>
                  <TableCell>{formatSite(forecast.site_id, forecast.site_name)}</TableCell>
                  <TableCell>
                    {new Date(forecast.forecast_date).toLocaleDateString()}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-1">
                      {forecast.edited && <Badge variant="warning" className="text-xs">edited</Badge>}
                      {formatNumber(forecast.forecast_p10)}
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    <span className="font-bold">{formatNumber(forecast.forecast_p50)}</span>
                  </TableCell>
                  <TableCell className="text-right">
                    {formatNumber(forecast.forecast_median ?? forecast.forecast_p50)}
                  </TableCell>
                  <TableCell className="text-right">{formatNumber(forecast.forecast_p90)}</TableCell>
                  <TableCell className="text-center">
                    {forecast.forecast_confidence != null ? (
                      <TooltipProvider>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Badge variant={
                              forecast.forecast_confidence >= 0.85 ? 'success' :
                              forecast.forecast_confidence >= 0.7 ? 'warning' :
                              'destructive'
                            } className="gap-1">
                              <ShieldCheck className="h-3 w-3" />
                              {(forecast.forecast_confidence * 100).toFixed(0)}%
                            </Badge>
                          </TooltipTrigger>
                          <TooltipContent>
                            <p>Conformal prediction confidence</p>
                            {forecast.conformal_method && (
                              <p className="text-xs">Method: {forecast.conformal_method}</p>
                            )}
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    ) : (
                      <span className="text-xs text-muted-foreground">—</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-1">
                      <Badge variant="secondary">{forecast.forecast_type || 'statistical'}</Badge>
                      {editMode && (
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={(e) => { e.stopPropagation(); handleEditClick(forecast, index); }}
                              >
                                <Pencil className="h-3 w-3" />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>Edit forecast</TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                      )}
                      {forecast.edited && (
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={(e) => { e.stopPropagation(); handleUndoEdit(forecast, index); }}
                              >
                                <Undo className="h-3 w-3" />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>Undo edit</TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                      )}
                    </div>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-col gap-1">
                      <Badge variant="outline">{forecast.forecast_source || 'legacy'}</Badge>
                      {forecast.forecast_run_id && (
                        <span className="text-xs text-muted-foreground">run {forecast.forecast_run_id}</span>
                      )}
                    </div>
                  </TableCell>
                  <TableCell className="text-center">
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={(e) => {
                              e.stopPropagation();
                              setPeggingTarget({
                                productId: forecast.product_id,
                                siteId: forecast.site_id,
                                demandDate: forecast.forecast_date,
                                demandType: 'FORECAST',
                              });
                            }}
                          >
                            <GitBranch className="h-4 w-4" />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>View Pegging</TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {forecasts.length > 100 && (
            <p className="text-sm text-muted-foreground mt-2">
              Showing first 100 of {forecasts.length} forecasts
            </p>
          )}
        </CardContent>
      </Card>

      {/* Level Pegging Gantt */}
      {peggingTarget && effectiveConfigId && (
        <LevelPeggingGantt
          configId={effectiveConfigId}
          productId={peggingTarget.productId}
          siteId={peggingTarget.siteId}
          demandDate={peggingTarget.demandDate}
          demandType={peggingTarget.demandType}
          onClose={() => setPeggingTarget(null)}
        />
      )}

      {/* Delta Comparison Dialog */}
      <Modal
        open={compareDialogOpen}
        onClose={() => setCompareDialogOpen(false)}
        title="Demand Plan Delta Analysis"
        size="lg"
      >
        {deltaData.length === 0 ? (
          <Alert variant="info">
            No significant changes detected (delta threshold: 5%)
          </Alert>
        ) : (
          <>
            <p className="text-sm text-muted-foreground mb-4">
              Showing changes greater than 5% between versions
            </p>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Product</TableHead>
                  <TableHead>Site</TableHead>
                  <TableHead>Date</TableHead>
                  <TableHead className="text-right">Previous (Median)</TableHead>
                  <TableHead className="text-right">Current (Median)</TableHead>
                  <TableHead className="text-right">Previous (Median)</TableHead>
                  <TableHead className="text-right">Current (Median)</TableHead>
                  <TableHead className="text-right">Delta</TableHead>
                  <TableHead className="text-right">% Change</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {deltaData.slice(0, 50).map((delta, index) => (
                  <TableRow key={index}>
                    <TableCell>{formatProduct(delta.product_id, delta.product_name)}</TableCell>
                    <TableCell>{formatSite(delta.site_id, delta.site_name)}</TableCell>
                    <TableCell>
                      {new Date(delta.forecast_date).toLocaleDateString()}
                    </TableCell>
                    <TableCell className="text-right">{formatNumber(delta.version1_p50)}</TableCell>
                    <TableCell className="text-right">{formatNumber(delta.version2_p50)}</TableCell>
                    <TableCell className="text-right">{formatNumber(delta.version1_median ?? delta.version1_p50)}</TableCell>
                    <TableCell className="text-right">{formatNumber(delta.version2_median ?? delta.version2_p50)}</TableCell>
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-1">
                        {delta.delta_p50 > 0 ? (
                          <TrendingUp className="h-4 w-4 text-emerald-600" />
                        ) : (
                          <TrendingDown className="h-4 w-4 text-red-600" />
                        )}
                        {formatNumber(Math.abs(delta.delta_p50))}
                      </div>
                    </TableCell>
                    <TableCell className="text-right">
                      <Badge variant={delta.delta_pct > 0 ? 'success' : 'destructive'}>
                        {delta.delta_pct >= 0 ? '+' : ''}{delta.delta_pct.toFixed(1)}%
                      </Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {deltaData.length > 50 && (
              <p className="text-sm text-muted-foreground mt-2">
                Showing first 50 of {deltaData.length} changes
              </p>
            )}
          </>
        )}
        <div className="flex justify-end mt-4">
          <Button variant="outline" onClick={() => setCompareDialogOpen(false)}>Close</Button>
        </div>
      </Modal>

      {/* Edit Forecast Dialog */}
      <Modal
        open={editDialogOpen}
        onClose={() => setEditDialogOpen(false)}
        title="Edit Forecast"
        size="md"
      >
        {selectedForecastForEdit && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-sm text-muted-foreground">Product</p>
                <p className="font-medium">{formatProduct(selectedForecastForEdit.product_id, selectedForecastForEdit.product_name)}</p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">Site</p>
                <p className="font-medium">{formatSite(selectedForecastForEdit.site_id, selectedForecastForEdit.site_name)}</p>
              </div>
              <div className="col-span-2">
                <p className="text-sm text-muted-foreground">Forecast Date</p>
                <p className="font-medium">
                  {new Date(selectedForecastForEdit.forecast_date).toLocaleDateString()}
                </p>
              </div>
            </div>

            <div>
              <h4 className="font-medium mb-2">Adjust Forecast Values</h4>
              <Alert variant="info" className="mb-4">
                P10 &lt; P50 &lt; P90 (Low &lt; Most Likely &lt; High)
              </Alert>

              <div className="grid grid-cols-3 gap-4">
                <div>
                  <Label>P10 (Low)</Label>
                  <Input
                    type="number"
                    value={editValues.p10}
                    onChange={(e) => setEditValues(prev => ({ ...prev, p10: Number(e.target.value) }))}
                    min={0}
                    className="mt-1"
                  />
                </div>
                <div>
                  <Label>Median (Most Likely)</Label>
                  <Input
                    type="number"
                    value={editValues.p50}
                    onChange={(e) => setEditValues(prev => ({ ...prev, p50: Number(e.target.value) }))}
                    min={0}
                    className="mt-1"
                  />
                </div>
                <div>
                  <Label>P90 (High)</Label>
                  <Input
                    type="number"
                    value={editValues.p90}
                    onChange={(e) => setEditValues(prev => ({ ...prev, p90: Number(e.target.value) }))}
                    min={0}
                    className="mt-1"
                  />
                </div>
              </div>
              <div className="mt-4">
                <Label>Reason for Override (optional)</Label>
                <Textarea
                  value={editValues.reason}
                  onChange={(e) => setEditValues(prev => ({ ...prev, reason: e.target.value }))}
                  placeholder="e.g., Promotional event, Market insight, Customer feedback..."
                  rows={2}
                  className="mt-1"
                />
              </div>

              {(editValues.p10 > editValues.p50 || editValues.p50 > editValues.p90) && (
                <Alert variant="warning" className="mt-4">
                  Warning: P10 should be less than P50, and P50 should be less than P90
                </Alert>
              )}
            </div>
          </div>
        )}
        <div className="flex justify-end gap-2 mt-6">
          <Button variant="outline" onClick={() => setEditDialogOpen(false)}>Cancel</Button>
          <Button
            onClick={handleSaveEdit}
            disabled={editValues.p10 > editValues.p50 || editValues.p50 > editValues.p90}
            leftIcon={<Save className="h-4 w-4" />}
          >
            Apply Change
          </Button>
        </div>
      </Modal>
    </div>
  );
};

export default DemandPlanView;
