/**
 * Scenario Comparison Page — Full-page side-by-side plan comparison.
 *
 * Compares: ERP Baseline vs Autonomy Plan of Record
 * Shows: KPI delta cards, time-series overlay chart, category breakdown
 */
import React, { useState, useEffect, useCallback } from 'react';
import {
  Card, CardContent, Badge, Button, Alert,
} from '../../components/common';
import {
  GitCompare, TrendingUp, TrendingDown, Minus, RefreshCw, BarChart3,
} from 'lucide-react';
import {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import { api } from '../../services/api';
import { useActiveConfig } from '../../contexts/ActiveConfigContext';
import { cn } from '@azirella-ltd/autonomy-frontend';

const Delta = ({ value, inverse }) => {
  if (!value || value === 0) return <span className="text-muted-foreground">0%</span>;
  const isGood = inverse ? value < 0 : value > 0;
  return (
    <span className={cn("font-bold", isGood ? "text-green-600" : "text-red-600")}>
      {value > 0 ? '+' : ''}{value}%
    </span>
  );
};

export default function ScenarioComparison() {
  const { effectiveConfigId } = useActiveConfig();
  const [data, setData] = useState(null);
  const [planData, setPlanData] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!effectiveConfigId) return;
    setLoading(true);
    try {
      const [compRes, planRes] = await Promise.allSettled([
        api.get('/scenario-planning/erp-comparison', { params: { config_id: effectiveConfigId } }),
        api.get('/demand-plan/aggregated', { params: { config_id: effectiveConfigId, time_bucket: 'week' } }),
      ]);
      if (compRes.status === 'fulfilled') setData(compRes.value.data);
      if (planRes.status === 'fulfilled') setPlanData(planRes.value.data);
    } catch (err) {
      console.error('Comparison load failed:', err);
    } finally {
      setLoading(false);
    }
  }, [effectiveConfigId]);

  useEffect(() => { load(); }, [load]);

  const comp = data?.comparison || {};
  const erp = data?.erp_baseline;
  const ai = data?.autonomy_plan;
  const series = planData?.series || [];

  if (!erp && !ai && !loading) {
    return (
      <div className="max-w-5xl mx-auto px-4 py-8">
        <Alert>No comparison data available. Run provisioning to generate the Plan of Record and ERP baseline.</Alert>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      <div className="flex justify-between items-center mb-6">
        <div className="flex items-center gap-3">
          <GitCompare className="h-8 w-8 text-primary" />
          <div>
            <h1 className="text-2xl font-bold">Plan Comparison</h1>
            <p className="text-sm text-muted-foreground">
              ERP Baseline vs Autonomy AI Plan of Record
            </p>
          </div>
        </div>
        <Button variant="outline" onClick={load} disabled={loading}
          leftIcon={<RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />}>
          Refresh
        </Button>
      </div>

      {/* KPI Delta Cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6">
        {[
          { key: 'total_orders', label: 'Total Orders', desc: 'Plan complexity' },
          { key: 'total_planned_qty', label: 'Total Qty', desc: 'Volume planned' },
          { key: 'avg_order_qty', label: 'Avg Order Size', desc: 'Lot sizing', inverse: true },
          { key: 'periods', label: 'Horizon', desc: 'Planning periods' },
          { key: 'products', label: 'Products', desc: 'Coverage' },
        ].map(({ key, label, desc, inverse }) => {
          const d = comp[key];
          if (!d) return null;
          const fmt = (v) => typeof v === 'number' ? Math.round(v).toLocaleString() : '—';
          return (
            <Card key={key}>
              <CardContent className="pt-4">
                <p className="text-xs text-muted-foreground uppercase tracking-wide">{label}</p>
                <div className="grid grid-cols-2 gap-2 my-2">
                  <div>
                    <p className="text-[10px] text-amber-600">ERP</p>
                    <p className="text-lg font-medium">{fmt(d.erp)}</p>
                  </div>
                  <div>
                    <p className="text-[10px] text-blue-600">AI</p>
                    <p className="text-lg font-bold">{fmt(d.autonomy)}</p>
                  </div>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-[10px] text-muted-foreground">{desc}</span>
                  <Delta value={d.delta_pct} inverse={inverse} />
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      {/* Time Series Chart */}
      {series.length > 0 && (
        <Card className="mb-6">
          <CardContent className="pt-4">
            <h2 className="text-lg font-semibold mb-3">Demand Plan — Conformal Prediction Intervals</h2>
            <ResponsiveContainer width="100%" height={350}>
              <ComposedChart data={series.slice(-26)}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 10 }} />
                <Tooltip contentStyle={{ fontSize: 11 }} />
                <Legend wrapperStyle={{ fontSize: 10 }} />
                <Bar dataKey="p50" fill="#6366f1" fillOpacity={0.3} name="Plan of Record (Median)" />
                {series[0]?.actual != null && (
                  <Line type="monotone" dataKey="actual" stroke="#ef4444" strokeWidth={2} name="Actual Demand" dot={{ r: 2 }} />
                )}
                <Line type="monotone" dataKey="p10" stroke="#86efac" strokeDasharray="4 3" name="P10" dot={false} />
                <Line type="monotone" dataKey="p90" stroke="#fbbf24" strokeDasharray="4 3" name="P90" dot={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      )}

      {/* Summary */}
      <div className="grid grid-cols-2 gap-6">
        {erp && (
          <Card className="border-amber-200">
            <CardContent className="pt-4">
              <h3 className="font-semibold text-amber-700 mb-3">ERP Baseline</h3>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between"><span>Orders</span><span className="font-medium">{erp.total_orders.toLocaleString()}</span></div>
                <div className="flex justify-between"><span>Total Qty</span><span className="font-medium">{Math.round(erp.total_planned_qty).toLocaleString()}</span></div>
                <div className="flex justify-between"><span>Avg Order</span><span className="font-medium">{erp.avg_order_qty.toLocaleString()}</span></div>
                <div className="flex justify-between"><span>Products</span><span>{erp.products}</span></div>
                <div className="flex justify-between"><span>Sites</span><span>{erp.sites}</span></div>
                <div className="flex justify-between"><span>Period</span><span>{erp.start_date} — {erp.end_date}</span></div>
              </div>
            </CardContent>
          </Card>
        )}
        {ai && (
          <Card className="border-blue-200">
            <CardContent className="pt-4">
              <h3 className="font-semibold text-blue-700 mb-3">Autonomy AI Plan</h3>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between"><span>Orders</span><span className="font-bold">{ai.total_orders.toLocaleString()}</span></div>
                <div className="flex justify-between"><span>Total Qty</span><span className="font-bold">{Math.round(ai.total_planned_qty).toLocaleString()}</span></div>
                <div className="flex justify-between"><span>Avg Order</span><span className="font-bold">{ai.avg_order_qty.toLocaleString()}</span></div>
                <div className="flex justify-between"><span>Products</span><span>{ai.products}</span></div>
                <div className="flex justify-between"><span>Sites</span><span>{ai.sites}</span></div>
                <div className="flex justify-between"><span>Period</span><span>{ai.start_date} — {ai.end_date}</span></div>
              </div>
            </CardContent>
          </Card>
        )}
      </div>

      {/* Inventory position */}
      {comp.avg_inventory && (
        <Card className="mt-6">
          <CardContent className="pt-4">
            <h3 className="font-semibold mb-3">Current Inventory Position</h3>
            <div className="grid grid-cols-3 gap-4 text-center">
              <div>
                <p className="text-2xl font-bold">{Math.round(comp.avg_inventory).toLocaleString()}</p>
                <p className="text-xs text-muted-foreground">Avg Inventory</p>
              </div>
              <div>
                <p className="text-2xl font-bold">{Math.round(comp.total_inventory).toLocaleString()}</p>
                <p className="text-xs text-muted-foreground">Total Inventory</p>
              </div>
              <div>
                <p className="text-2xl font-bold">{comp.dos_ratio}</p>
                <p className="text-xs text-muted-foreground">Days of Supply Ratio</p>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
