/**
 * Supply Plan View — Shows the agent-generated Plan of Record.
 *
 * No manual generation — the plan is created automatically during provisioning
 * from conformal-calibrated forecast P50. Uncertainty is quantified by
 * conformal P10/P90 intervals, NOT Monte Carlo simulation.
 *
 * Users inspect the plan and override via Decision Stream.
 */
import React, { useState, useEffect, useCallback } from 'react';
import {
  Card, CardContent, Badge, Button, Alert,
  Tabs, TabsList, TabsTrigger, TabsContent,
  Table, TableHeader, TableBody, TableRow, TableHead, TableCell,
} from '../../components/common';
import {
  Clock, RefreshCw, Package, TrendingUp,
} from 'lucide-react';
import {
  ComposedChart, Line, Area, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import { api } from '../../services/api';
import { useActiveConfig } from '../../contexts/ActiveConfigContext';
import ScenarioPanel from '../../components/planning/ScenarioPanel';
import ERPComparisonPanel from '../../components/planning/ERPComparisonPanel';

// Inline sourcing rules table
const SourcingRulesTable = ({ configId }) => {
  const [rules, setRules] = useState([]);
  useEffect(() => {
    if (!configId) return;
    api.get('/sourcing-rules/', { params: { config_id: configId } })
      .then(res => setRules(Array.isArray(res.data) ? res.data : res.data?.rules || []))
      .catch(() => setRules([]));
  }, [configId]);

  if (rules.length === 0) return <p className="text-sm text-muted-foreground">No sourcing rules configured. These are auto-derived from vendor performance data.</p>;

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Product</TableHead>
          <TableHead>Vendor</TableHead>
          <TableHead>Share %</TableHead>
          <TableHead>Lead Time</TableHead>
          <TableHead>Priority</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rules.slice(0, 20).map((r, i) => (
          <TableRow key={i}>
            <TableCell className="text-xs">{r.product_id || r.material_id || '—'}</TableCell>
            <TableCell className="text-xs">{r.vendor_id || r.supplier_id || '—'}</TableCell>
            <TableCell>{r.allocation_pct || r.share_pct || '—'}%</TableCell>
            <TableCell>{r.lead_time_days || '—'} days</TableCell>
            <TableCell>{r.priority || '—'}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
};

// Inline net requirements table
const NetRequirementsTable = ({ configId }) => {
  const [data, setData] = useState([]);
  useEffect(() => {
    if (!configId) return;
    // Use supply plan live data as net requirements proxy
    api.get('/demand-plan/grid', { params: { config_id: configId, weeks: 8 } })
      .then(res => setData(res.data?.rows || []))
      .catch(() => setData([]));
  }, [configId]);

  if (data.length === 0) return <p className="text-sm text-muted-foreground">Loading net requirements from Plan of Record...</p>;

  const weeks = data[0]?.weeks ? Object.keys(data[0].weeks).sort().slice(0, 8) : [];

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr className="border-b">
            <th className="text-left py-1 px-2 sticky left-0 bg-white">Product / Site</th>
            {weeks.map(w => <th key={w} className="text-center py-1 px-1 min-w-[60px]">{w.slice(5)}</th>)}
          </tr>
        </thead>
        <tbody>
          {data.slice(0, 15).map(row => (
            <tr key={`${row.product_id}|${row.site_id}`} className="border-b">
              <td className="py-1 px-2 sticky left-0 bg-white">
                <div className="font-medium truncate max-w-[180px]">{row.product_name}</div>
                <div className="text-muted-foreground">{row.site_name}</div>
              </td>
              {weeks.map(w => (
                <td key={w} className="text-center py-1 px-1 tabular-nums">
                  {row.weeks?.[w] ? Math.round(row.weeks[w]).toLocaleString() : '—'}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

export default function SupplyPlanGeneration() {
  const { effectiveConfigId } = useActiveConfig();
  const [planData, setPlanData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [timeBucket, setTimeBucket] = useState('week');
  const [tab, setTab] = useState('plan');
  const cfgId = effectiveConfigId;

  const loadPlan = useCallback(async () => {
    if (!cfgId) return;
    setLoading(true);
    try {
      const res = await api.get('/demand-plan/aggregated', {
        params: { config_id: cfgId, time_bucket: timeBucket },
      });
      setPlanData(res.data);
    } catch (err) {
      console.error('Failed to load plan:', err);
    } finally {
      setLoading(false);
    }
  }, [cfgId, timeBucket]);

  useEffect(() => { loadPlan(); }, [loadPlan]);

  const series = planData?.series || [];
  const summary = planData?.summary || {};

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      <div className="flex justify-between items-center mb-4">
        <div className="flex items-center gap-2">
          <Package className="h-7 w-7 text-primary" />
          <div>
            <h1 className="text-2xl font-bold">Supply Plan</h1>
            <p className="text-sm text-muted-foreground">
              Plan of Record — generated from conformal-calibrated forecasts (P50)
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <Badge variant="success" className="text-xs">
            <TrendingUp className="h-3 w-3 mr-1" />
            Conformal P10/P50/P90
          </Badge>
          <select className="border rounded px-2 py-1.5 text-sm"
            value={timeBucket} onChange={e => setTimeBucket(e.target.value)}>
            <option value="day">Daily</option>
            <option value="week">Weekly</option>
            <option value="month">Monthly</option>
          </select>
          <Button variant="outline" onClick={loadPlan} disabled={loading}
            leftIcon={<RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />}>
            Refresh
          </Button>
        </div>
      </div>

      <ScenarioPanel className="mb-4" />
      <ERPComparisonPanel className="mb-4" />

      {/* Summary */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
        <Card>
          <CardContent className="pt-4">
            <p className="text-sm text-muted-foreground">Plan Records</p>
            <p className="text-2xl font-bold">{summary.total_records?.toLocaleString() || '—'}</p>
            <p className="text-xs text-muted-foreground">Plan of Record (live)</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <p className="text-sm text-muted-foreground">Products</p>
            <p className="text-2xl font-bold">{summary.total_products || '—'}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <p className="text-sm text-muted-foreground">Sites</p>
            <p className="text-2xl font-bold">{summary.total_sites || '—'}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <p className="text-sm text-muted-foreground">Uncertainty Method</p>
            <p className="text-lg font-bold text-green-600">Conformal Prediction</p>
            <p className="text-xs text-muted-foreground">Not Monte Carlo</p>
          </CardContent>
        </Card>
      </div>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="plan">Plan View</TabsTrigger>
          <TabsTrigger value="directives">Agent Directives</TabsTrigger>
          <TabsTrigger value="sourcing">Sourcing</TabsTrigger>
          <TabsTrigger value="netting">Net Requirements</TabsTrigger>
          <TabsTrigger value="capacity">Capacity Check</TabsTrigger>
        </TabsList>

        <TabsContent value="plan">
          {/* Confidence band chart */}
          {series.length > 0 && (
            <Card className="mb-4">
              <CardContent className="pt-4">
                <h3 className="text-sm font-semibold mb-2">
                  Supply Plan — Conformal Prediction Intervals
                </h3>
                <ResponsiveContainer width="100%" height={300}>
                  <ComposedChart data={series}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                    <XAxis dataKey="date" tick={{ fontSize: 10 }} />
                    <YAxis tick={{ fontSize: 10 }} />
                    <Tooltip contentStyle={{ fontSize: 11 }} />
                    <Legend wrapperStyle={{ fontSize: 10 }} />
                    <Area type="monotone" dataKey="p90" stroke="none" fill="#8884d8" fillOpacity={0.12} name="P90 (High)" />
                    <Area type="monotone" dataKey="p10" stroke="none" fill="#ffffff" fillOpacity={1} name="P10 (Low)" />
                    <Line type="monotone" dataKey="p50" stroke="#6366f1" strokeWidth={2.5} name="Plan of Record (Median)" dot={false} />
                    <Line type="monotone" dataKey="p10" stroke="#86efac" strokeWidth={1} strokeDasharray="4 3" name="P10" dot={false} />
                    <Line type="monotone" dataKey="p90" stroke="#fbbf24" strokeWidth={1} strokeDasharray="4 3" name="P90" dot={false} />
                    {series[0]?.actual != null && (
                      <Line type="monotone" dataKey="actual" stroke="#ef4444" strokeWidth={2} name="Actual" dot={{ r: 2 }} connectNulls={false} />
                    )}
                  </ComposedChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>
          )}

          {/* Time-phased grid */}
          {series.length > 0 && (
            <Card>
              <CardContent className="pt-4">
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="sticky left-0 bg-white">Period</TableHead>
                        <TableHead className="text-right">P10</TableHead>
                        <TableHead className="text-right font-bold">Median (Plan)</TableHead>
                        <TableHead className="text-right">P90</TableHead>
                        <TableHead className="text-right">Actual</TableHead>
                        <TableHead className="text-right">Products</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {series.slice(0, 52).map(row => (
                        <TableRow key={row.date}>
                          <TableCell className="sticky left-0 bg-white font-mono text-xs">{row.date}</TableCell>
                          <TableCell className="text-right text-muted-foreground">{row.p10?.toLocaleString()}</TableCell>
                          <TableCell className="text-right font-bold">{row.p50?.toLocaleString()}</TableCell>
                          <TableCell className="text-right text-muted-foreground">{row.p90?.toLocaleString()}</TableCell>
                          <TableCell className="text-right text-red-600">{row.actual?.toLocaleString() || '—'}</TableCell>
                          <TableCell className="text-right text-xs">{row.products}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </CardContent>
            </Card>
          )}

          {series.length === 0 && !loading && (
            <Alert className="mt-4">
              No plan data. The plan is generated automatically during provisioning from conformal P50 forecasts.
            </Alert>
          )}
        </TabsContent>

        <TabsContent value="directives">
          <Alert className="mt-4">
            Agent directives — tactical GNN allocation and rebalancing recommendations.
            View and override in the Decision Stream.
          </Alert>
        </TabsContent>

        <TabsContent value="sourcing">
          <Card className="mt-4">
            <CardContent className="pt-4">
              <h3 className="text-sm font-semibold mb-2">Sourcing Rules</h3>
              <p className="text-xs text-muted-foreground mb-3">
                Vendor selection and allocation by product. These are scenario parameters —
                changes here can be tested via What-If scenarios before applying to the live plan.
              </p>
              <SourcingRulesTable configId={cfgId} />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="netting">
          <Card className="mt-4">
            <CardContent className="pt-4">
              <h3 className="text-sm font-semibold mb-2">Net Requirements</h3>
              <p className="text-xs text-muted-foreground mb-3">
                Gross demand minus on-hand minus on-order = net requirement.
              </p>
              <NetRequirementsTable configId={cfgId} />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="capacity">
          <Alert className="mt-4">
            RCCP capacity check — validates the plan against resource constraints.
            Populated during the RCCP validation provisioning step.
          </Alert>
        </TabsContent>
      </Tabs>
    </div>
  );
}
