/**
 * ATP/CTP History Chart Component
 *
 * Phase 5: Historical Trend Visualization
 * Displays ATP and CTP trends over multiple rounds with P10/P50/P90 confidence bands.
 *
 * Props:
 * - scenarioId: Scenario ID
 * - scenarioUserId: ScenarioUser ID
 * - showCTP: Whether to show CTP history (for manufacturers)
 */

import React, { useState, useEffect } from 'react';
import {
  Card,
  CardContent,
  Badge,
  Alert,
  AlertDescription,
  Spinner,
  Button,
} from '../common';
import {
  AreaChart,
  Area,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts';
import {
  TrendingUp,
  TrendingDown,
  RefreshCw,
  Info as InfoIcon,
  AlertTriangle as WarningIcon,
  History,
} from 'lucide-react';
import simulationApi from '../../services/api';
import { cn } from '@azirella-ltd/autonomy-frontend';

const ATPHistoryChart = ({
  scenarioId,
  scenarioUserId,
  showCTP = false,
  limit = 20,
}) => {
  const [historyData, setHistoryData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [viewMode, setViewMode] = useState('atp'); // 'atp' or 'ctp'

  useEffect(() => {
    fetchHistory();
  }, [scenarioId, scenarioUserId, limit]);

  const fetchHistory = async () => {
    if (!scenarioId || !scenarioUserId) return;

    setLoading(true);
    setError(null);

    try {
      const data = await simulationApi.getATPHistory(scenarioId, scenarioUserId, limit);
      setHistoryData(data);
    } catch (err) {
      console.error('Error fetching ATP/CTP history:', err);
      setError(err.response?.data?.detail || 'Failed to fetch history');
    } finally {
      setLoading(false);
    }
  };

  // Custom tooltip for ATP history
  const ATPTooltip = ({ active, payload }) => {
    if (!active || !payload || payload.length === 0) {
      return null;
    }

    const data = payload[0].payload;

    return (
      <Card className="p-2 bg-white/95 dark:bg-card/95 shadow-lg border">
        <CardContent className="p-2">
          <h4 className="font-semibold text-sm mb-2">Round {data.round}</h4>
          <div className="space-y-1.5 text-xs">
            <div className="flex justify-between gap-4">
              <span className="text-emerald-600">P90 (Optimistic):</span>
              <span className="font-semibold">{data.atp_p90} units</span>
            </div>
            <div className="flex justify-between gap-4">
              <span className="text-primary">Median:</span>
              <span className="font-semibold">{data.atp_p50} units</span>
            </div>
            <div className="flex justify-between gap-4">
              <span className="text-amber-600">P10 (Pessimistic):</span>
              <span className="font-semibold">{data.atp_p10} units</span>
            </div>
            <div className="flex justify-between gap-4 pt-1.5 border-t border-border">
              <span className="text-muted-foreground">On-Hand:</span>
              <span className="font-semibold">{data.on_hand}</span>
            </div>
            <div className="flex justify-between gap-4">
              <span className="text-muted-foreground">Scheduled Receipts:</span>
              <span className="font-semibold text-emerald-600">+{data.scheduled_receipts}</span>
            </div>
            <div className="flex justify-between gap-4">
              <span className="text-muted-foreground">Allocated:</span>
              <span className="font-semibold text-red-600">-{data.allocated_orders}</span>
            </div>
          </div>
        </CardContent>
      </Card>
    );
  };

  // Custom tooltip for CTP history
  const CTPTooltip = ({ active, payload }) => {
    if (!active || !payload || payload.length === 0) {
      return null;
    }

    const data = payload[0].payload;

    return (
      <Card className="p-2 bg-white/95 dark:bg-card/95 shadow-lg border">
        <CardContent className="p-2">
          <h4 className="font-semibold text-sm mb-2">Round {data.round}</h4>
          <div className="space-y-1.5 text-xs">
            <div className="flex justify-between gap-4">
              <span className="text-emerald-600">P90 (Optimistic):</span>
              <span className="font-semibold">{data.ctp_p90} units</span>
            </div>
            <div className="flex justify-between gap-4">
              <span className="text-primary">Median:</span>
              <span className="font-semibold">{data.ctp_p50} units</span>
            </div>
            <div className="flex justify-between gap-4">
              <span className="text-amber-600">P10 (Pessimistic):</span>
              <span className="font-semibold">{data.ctp_p10} units</span>
            </div>
            <div className="flex justify-between gap-4 pt-1.5 border-t border-border">
              <span className="text-muted-foreground">Capacity:</span>
              <span className="font-semibold">{data.production_capacity}</span>
            </div>
            <div className="flex justify-between gap-4">
              <span className="text-muted-foreground">Committed:</span>
              <span className="font-semibold text-amber-600">{data.commitments}</span>
            </div>
            <div className="flex justify-between gap-4">
              <span className="text-muted-foreground">Available:</span>
              <span className="font-semibold text-emerald-600">{data.available_capacity}</span>
            </div>
            {data.component_constrained && (
              <div className="pt-1 text-red-600">
                <WarningIcon className="inline h-3 w-3 mr-1" />
                Component constrained
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    );
  };

  if (loading) {
    return (
      <div className="flex justify-center p-8">
        <Spinner size="lg" />
      </div>
    );
  }

  if (error) {
    return (
      <Alert variant="error">
        <WarningIcon className="h-4 w-4" />
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }

  if (!historyData) {
    return (
      <Alert variant="info">
        <InfoIcon className="h-4 w-4" />
        <AlertDescription>No history data available</AlertDescription>
      </Alert>
    );
  }

  const hasATPHistory = historyData.history && historyData.history.length > 0;
  const hasCTPHistory = historyData.ctp_history && historyData.ctp_history.length > 0;

  // Calculate trend indicators
  const atpTrend = hasATPHistory && historyData.history.length >= 2
    ? historyData.history[historyData.history.length - 1].atp_p50 - historyData.history[historyData.history.length - 2].atp_p50
    : 0;

  const ctpTrend = hasCTPHistory && historyData.ctp_history.length >= 2
    ? historyData.ctp_history[historyData.ctp_history.length - 1].ctp_p50 - historyData.ctp_history[historyData.ctp_history.length - 2].ctp_p50
    : 0;

  return (
    <Card className="mb-4">
      <CardContent className="pt-4">
        <div className="space-y-4">
          {/* Header */}
          <div className="flex justify-between items-center">
            <h3 className="text-lg font-semibold flex items-center gap-2">
              <History className="h-5 w-5 text-primary" />
              ATP/CTP History
            </h3>
            <div className="flex gap-2 items-center">
              {/* View mode toggle */}
              {showCTP && hasCTPHistory && (
                <div className="flex rounded-lg border overflow-hidden">
                  <Button
                    variant={viewMode === 'atp' ? 'default' : 'ghost'}
                    size="sm"
                    onClick={() => setViewMode('atp')}
                    className="rounded-none"
                  >
                    ATP
                  </Button>
                  <Button
                    variant={viewMode === 'ctp' ? 'default' : 'ghost'}
                    size="sm"
                    onClick={() => setViewMode('ctp')}
                    className="rounded-none"
                  >
                    CTP
                  </Button>
                </div>
              )}

              {/* Trend indicator */}
              {viewMode === 'atp' && hasATPHistory && (
                <Badge
                  variant={atpTrend >= 0 ? 'success' : 'warning'}
                  className="gap-1"
                >
                  {atpTrend >= 0 ? (
                    <TrendingUp className="h-3 w-3" />
                  ) : (
                    <TrendingDown className="h-3 w-3" />
                  )}
                  {atpTrend >= 0 ? '+' : ''}{atpTrend}
                </Badge>
              )}
              {viewMode === 'ctp' && hasCTPHistory && (
                <Badge
                  variant={ctpTrend >= 0 ? 'success' : 'warning'}
                  className="gap-1"
                >
                  {ctpTrend >= 0 ? (
                    <TrendingUp className="h-3 w-3" />
                  ) : (
                    <TrendingDown className="h-3 w-3" />
                  )}
                  {ctpTrend >= 0 ? '+' : ''}{ctpTrend}
                </Badge>
              )}

              <Button
                variant="ghost"
                size="sm"
                onClick={fetchHistory}
                className="h-8 w-8 p-0"
              >
                <RefreshCw className="h-4 w-4" />
              </Button>
            </div>
          </div>

          {/* No data message */}
          {!hasATPHistory && !hasCTPHistory && (
            <Alert variant="info">
              <InfoIcon className="h-4 w-4" />
              <AlertDescription>
                No historical data yet. ATP/CTP projections will be recorded as rounds are played.
              </AlertDescription>
            </Alert>
          )}

          {/* ATP History Chart */}
          {viewMode === 'atp' && hasATPHistory && (
            <div className="w-full h-[300px]">
              <ResponsiveContainer>
                <AreaChart
                  data={historyData.history}
                  margin={{ top: 20, right: 30, left: 20, bottom: 20 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                  <XAxis
                    dataKey="round"
                    label={{ value: 'Round', position: 'insideBottom', offset: -10 }}
                    tick={{ fontSize: 12 }}
                  />
                  <YAxis
                    label={{ value: 'ATP (Units)', angle: -90, position: 'insideLeft' }}
                    tick={{ fontSize: 12 }}
                  />
                  <Tooltip content={<ATPTooltip />} />
                  <Legend verticalAlign="top" height={36} />

                  {/* P10-P90 confidence band */}
                  <Area
                    type="monotone"
                    dataKey="atp_p90"
                    stackId="1"
                    stroke="none"
                    fill="rgba(34, 197, 94, 0.2)"
                    name="P90 Upper"
                  />
                  <Area
                    type="monotone"
                    dataKey="atp_p10"
                    stackId="2"
                    stroke="none"
                    fill="rgba(245, 158, 11, 0.2)"
                    name="P10 Lower"
                  />

                  {/* P50 median line */}
                  <Line
                    type="monotone"
                    dataKey="atp_p50"
                    stroke="hsl(var(--primary))"
                    strokeWidth={3}
                    dot={{ fill: 'hsl(var(--primary))', r: 4 }}
                    name="Median"
                  />

                  {/* Zero line */}
                  <ReferenceLine y={0} stroke="#ef4444" strokeDasharray="5 5" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* CTP History Chart */}
          {viewMode === 'ctp' && hasCTPHistory && (
            <div className="w-full h-[300px]">
              <ResponsiveContainer>
                <AreaChart
                  data={historyData.ctp_history}
                  margin={{ top: 20, right: 30, left: 20, bottom: 20 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                  <XAxis
                    dataKey="round"
                    label={{ value: 'Round', position: 'insideBottom', offset: -10 }}
                    tick={{ fontSize: 12 }}
                  />
                  <YAxis
                    label={{ value: 'CTP (Units)', angle: -90, position: 'insideLeft' }}
                    tick={{ fontSize: 12 }}
                  />
                  <Tooltip content={<CTPTooltip />} />
                  <Legend verticalAlign="top" height={36} />

                  {/* P10-P90 confidence band */}
                  <Area
                    type="monotone"
                    dataKey="ctp_p90"
                    stackId="1"
                    stroke="none"
                    fill="rgba(34, 197, 94, 0.2)"
                    name="P90 Upper"
                  />
                  <Area
                    type="monotone"
                    dataKey="ctp_p10"
                    stackId="2"
                    stroke="none"
                    fill="rgba(245, 158, 11, 0.2)"
                    name="P10 Lower"
                  />

                  {/* P50 median line */}
                  <Line
                    type="monotone"
                    dataKey="ctp_p50"
                    stroke="hsl(var(--primary))"
                    strokeWidth={3}
                    dot={{ fill: 'hsl(var(--primary))', r: 4 }}
                    name="Median"
                  />

                  {/* Capacity line */}
                  <Line
                    type="monotone"
                    dataKey="production_capacity"
                    stroke="#9333ea"
                    strokeWidth={2}
                    strokeDasharray="5 5"
                    dot={false}
                    name="Capacity"
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Summary stats */}
          {((viewMode === 'atp' && hasATPHistory) || (viewMode === 'ctp' && hasCTPHistory)) && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4">
              {viewMode === 'atp' && hasATPHistory && (
                <>
                  <Card className="bg-muted/50">
                    <CardContent className="pt-3 pb-2">
                      <p className="text-xs text-muted-foreground">Current ATP</p>
                      <p className="text-xl font-bold">
                        {historyData.history[historyData.history.length - 1]?.atp_p50 || 'N/A'}
                      </p>
                    </CardContent>
                  </Card>
                  <Card className="bg-muted/50">
                    <CardContent className="pt-3 pb-2">
                      <p className="text-xs text-muted-foreground">Avg ATP (P50)</p>
                      <p className="text-xl font-bold">
                        {Math.round(
                          historyData.history.reduce((sum, r) => sum + (r.atp_p50 || 0), 0) /
                            historyData.history.length
                        )}
                      </p>
                    </CardContent>
                  </Card>
                  <Card className="bg-muted/50">
                    <CardContent className="pt-3 pb-2">
                      <p className="text-xs text-muted-foreground">Min ATP (P10)</p>
                      <p className="text-xl font-bold text-amber-600">
                        {Math.min(...historyData.history.map(r => r.atp_p10 || 0))}
                      </p>
                    </CardContent>
                  </Card>
                  <Card className="bg-muted/50">
                    <CardContent className="pt-3 pb-2">
                      <p className="text-xs text-muted-foreground">Max ATP (P90)</p>
                      <p className="text-xl font-bold text-emerald-600">
                        {Math.max(...historyData.history.map(r => r.atp_p90 || 0))}
                      </p>
                    </CardContent>
                  </Card>
                </>
              )}
              {viewMode === 'ctp' && hasCTPHistory && (
                <>
                  <Card className="bg-muted/50">
                    <CardContent className="pt-3 pb-2">
                      <p className="text-xs text-muted-foreground">Current CTP</p>
                      <p className="text-xl font-bold">
                        {historyData.ctp_history[historyData.ctp_history.length - 1]?.ctp_p50 || 'N/A'}
                      </p>
                    </CardContent>
                  </Card>
                  <Card className="bg-muted/50">
                    <CardContent className="pt-3 pb-2">
                      <p className="text-xs text-muted-foreground">Avg CTP (P50)</p>
                      <p className="text-xl font-bold">
                        {Math.round(
                          historyData.ctp_history.reduce((sum, r) => sum + (r.ctp_p50 || 0), 0) /
                            historyData.ctp_history.length
                        )}
                      </p>
                    </CardContent>
                  </Card>
                  <Card className="bg-muted/50">
                    <CardContent className="pt-3 pb-2">
                      <p className="text-xs text-muted-foreground">Avg Capacity</p>
                      <p className="text-xl font-bold">
                        {Math.round(
                          historyData.ctp_history.reduce((sum, r) => sum + (r.production_capacity || 0), 0) /
                            historyData.ctp_history.length
                        )}
                      </p>
                    </CardContent>
                  </Card>
                  <Card className="bg-muted/50">
                    <CardContent className="pt-3 pb-2">
                      <p className="text-xs text-muted-foreground">Utilization</p>
                      <p className="text-xl font-bold">
                        {Math.round(
                          (1 - (historyData.ctp_history.reduce((sum, r) => sum + (r.available_capacity || 0), 0) /
                            historyData.ctp_history.reduce((sum, r) => sum + (r.production_capacity || 1), 0))) * 100
                        )}%
                      </p>
                    </CardContent>
                  </Card>
                </>
              )}
            </div>
          )}

          {/* Legend */}
          <div className="p-3 bg-muted/50 rounded-lg">
            <p className="text-xs text-muted-foreground">
              <strong className="text-emerald-600">P90</strong>: 90% probability ATP/CTP will be at or below this value
              <br />
              <strong className="text-primary">Median</strong>: Most likely outcome (50th percentile of the predictive distribution)
              <br />
              <strong className="text-amber-600">P10</strong>: 10% probability ATP/CTP will be at or below this value
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
};

export default ATPHistoryChart;
