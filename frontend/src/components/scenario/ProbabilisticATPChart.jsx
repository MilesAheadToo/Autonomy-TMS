/**
 * Probabilistic ATP Chart Component
 *
 * Phase 5: Stochastic Lead Times Integration
 * Displays ATP projection with P10/P50/P90 confidence bands based on
 * Monte Carlo simulation of stochastic lead times.
 *
 * Props:
 * - gameId: Game ID
 * - scenarioUserId: ScenarioUser ID
 * - nSimulations: Number of Monte Carlo simulations (default 100)
 */

import React, { useState, useEffect } from 'react';
import {
  Card,
  CardContent,
  Badge,
  Alert,
  AlertTitle,
  AlertDescription,
  Spinner,
  Button,
} from '../common';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts';
import {
  AlertTriangle as WarningIcon,
  CheckCircle2 as CheckIcon,
  Info as InfoIcon,
  RefreshCw,
  TrendingUp,
  TrendingDown,
} from 'lucide-react';
import simulationApi from '../../services/api';
import { cn } from '@azirella-ltd/autonomy-frontend';

const ProbabilisticATPChart = ({
  gameId,
  scenarioUserId,
  nSimulations = 100,
}) => {
  const [atpData, setAtpData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetchProbabilisticATP();
  }, [gameId, scenarioUserId, nSimulations]);

  const fetchProbabilisticATP = async () => {
    if (!gameId || !scenarioUserId) return;

    setLoading(true);
    setError(null);

    try {
      const data = await simulationApi.getATPProbabilistic(
        gameId,
        scenarioUserId,
        nSimulations
      );
      setAtpData(data);
    } catch (err) {
      console.error('Error fetching probabilistic ATP:', err);
      setError(err.response?.data?.detail || 'Failed to fetch probabilistic ATP');
    } finally {
      setLoading(false);
    }
  };

  // Custom tooltip for confidence bands
  const CustomTooltip = ({ active, payload }) => {
    if (!active || !payload || payload.length === 0) {
      return null;
    }

    const data = atpData;

    return (
      <Card className="p-2 bg-white/95 dark:bg-card/95 shadow-lg border">
        <CardContent className="p-2">
          <h4 className="font-semibold text-sm mb-2">ATP Confidence Bands</h4>
          <div className="space-y-1.5 text-xs">
            <div className="flex justify-between gap-4">
              <span className="text-muted-foreground">P90 (Optimistic):</span>
              <span className="font-semibold text-emerald-600">{data?.atp_p90} units</span>
            </div>
            <div className="flex justify-between gap-4">
              <span className="text-muted-foreground">Median:</span>
              <span className="font-semibold text-primary">{data?.atp_p50} units</span>
            </div>
            <div className="flex justify-between gap-4">
              <span className="text-muted-foreground">P10 (Pessimistic):</span>
              <span className="font-semibold text-amber-600">{data?.atp_p10} units</span>
            </div>
            <div className="flex justify-between gap-4 pt-1.5 border-t border-border">
              <span className="text-muted-foreground">Lead Time Mean:</span>
              <span className="font-semibold">{data?.lead_time_mean?.toFixed(1)} rounds</span>
            </div>
            <div className="flex justify-between gap-4">
              <span className="text-muted-foreground">Lead Time StdDev:</span>
              <span className="font-semibold">{data?.lead_time_stddev?.toFixed(2)} rounds</span>
            </div>
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

  if (!atpData) {
    return (
      <Alert variant="info">
        <InfoIcon className="h-4 w-4" />
        <AlertDescription>No probabilistic ATP data available</AlertDescription>
      </Alert>
    );
  }

  // Build chart data for visualization
  const chartData = [
    {
      name: 'Current',
      p10: atpData.atp_p10,
      p50: atpData.atp_p50,
      p90: atpData.atp_p90,
      deterministic: atpData.deterministic_atp,
    },
  ];

  // Calculate risk level based on P10
  const isAtRisk = atpData.atp_p10 < (atpData.safety_stock || 0);
  const isHealthy = atpData.atp_p10 >= (atpData.safety_stock || 0);

  return (
    <Card className="mb-4">
      <CardContent className="pt-4">
        <div className="space-y-4">
          {/* Header */}
          <div className="flex justify-between items-center">
            <h3 className="text-lg font-semibold flex items-center gap-2">
              <TrendingUp className="h-5 w-5 text-primary" />
              Probabilistic ATP Analysis
            </h3>
            <div className="flex gap-2 items-center">
              <Badge variant="secondary" className="text-xs">
                {nSimulations} simulations
              </Badge>
              {isHealthy ? (
                <Badge variant="success" className="gap-1">
                  <CheckIcon className="h-3 w-3" />
                  Low Risk
                </Badge>
              ) : (
                <Badge variant="warning" className="gap-1">
                  <WarningIcon className="h-3 w-3" />
                  At Risk
                </Badge>
              )}
              <Button
                variant="ghost"
                size="sm"
                onClick={fetchProbabilisticATP}
                className="h-8 w-8 p-0"
              >
                <RefreshCw className="h-4 w-4" />
              </Button>
            </div>
          </div>

          {/* Summary Cards */}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <Card className="bg-emerald-50 dark:bg-emerald-950/30">
              <CardContent className="pt-4 pb-3">
                <p className="text-xs text-emerald-600 dark:text-emerald-400 font-medium">
                  P90 (Optimistic)
                </p>
                <p className="text-2xl font-bold text-emerald-700 dark:text-emerald-300">
                  {atpData.atp_p90}
                </p>
                <p className="text-xs text-muted-foreground">units available</p>
              </CardContent>
            </Card>

            <Card className="bg-primary/10">
              <CardContent className="pt-4 pb-3">
                <p className="text-xs text-primary font-medium">
                  P50 (Median)
                </p>
                <p className="text-2xl font-bold text-primary">
                  {atpData.atp_p50}
                </p>
                <p className="text-xs text-muted-foreground">units available</p>
              </CardContent>
            </Card>

            <Card className="bg-amber-50 dark:bg-amber-950/30">
              <CardContent className="pt-4 pb-3">
                <p className="text-xs text-amber-600 dark:text-amber-400 font-medium">
                  P10 (Pessimistic)
                </p>
                <p className="text-2xl font-bold text-amber-700 dark:text-amber-300">
                  {atpData.atp_p10}
                </p>
                <p className="text-xs text-muted-foreground">units available</p>
              </CardContent>
            </Card>

            <Card className="bg-muted/50">
              <CardContent className="pt-4 pb-3">
                <p className="text-xs text-muted-foreground font-medium">
                  Deterministic
                </p>
                <p className="text-2xl font-bold">
                  {atpData.deterministic_atp}
                </p>
                <p className="text-xs text-muted-foreground">without variance</p>
              </CardContent>
            </Card>
          </div>

          {/* Lead Time Stats */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Card>
              <CardContent className="pt-4 pb-3">
                <h4 className="text-sm font-semibold mb-2">Lead Time Statistics</h4>
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <div>
                    <span className="text-muted-foreground">Mean:</span>
                    <span className="ml-2 font-semibold">
                      {atpData.lead_time_mean?.toFixed(2)} rounds
                    </span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Std Dev:</span>
                    <span className="ml-2 font-semibold">
                      {atpData.lead_time_stddev?.toFixed(2)} rounds
                    </span>
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="pt-4 pb-3">
                <h4 className="text-sm font-semibold mb-2">Inventory Components</h4>
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <div>
                    <span className="text-muted-foreground">On-Hand:</span>
                    <span className="ml-2 font-semibold">{atpData.on_hand_inventory}</span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Pipeline:</span>
                    <span className="ml-2 font-semibold">{atpData.scheduled_receipts_total}</span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Allocated:</span>
                    <span className="ml-2 font-semibold">{atpData.allocated_orders}</span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Safety Stock:</span>
                    <span className="ml-2 font-semibold">{atpData.safety_stock || 0}</span>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>

          {/* Confidence Band Visual */}
          <div className="w-full h-[200px]">
            <ResponsiveContainer>
              <AreaChart
                data={chartData}
                margin={{ top: 20, right: 30, left: 20, bottom: 20 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                <XAxis dataKey="name" />
                <YAxis />
                <Tooltip content={<CustomTooltip />} />

                {/* P10-P90 confidence band */}
                <Area
                  type="monotone"
                  dataKey="p90"
                  stackId="1"
                  stroke="none"
                  fill="rgba(34, 197, 94, 0.3)"
                  name="P90 (Optimistic)"
                />
                <Area
                  type="monotone"
                  dataKey="p50"
                  stackId="2"
                  stroke="hsl(var(--primary))"
                  strokeWidth={2}
                  fill="rgba(59, 130, 246, 0.3)"
                  name="Median"
                />
                <Area
                  type="monotone"
                  dataKey="p10"
                  stackId="3"
                  stroke="none"
                  fill="rgba(245, 158, 11, 0.3)"
                  name="P10 (Pessimistic)"
                />

                {/* Safety stock line if available */}
                {atpData.safety_stock > 0 && (
                  <ReferenceLine
                    y={atpData.safety_stock}
                    stroke="#ef4444"
                    strokeDasharray="5 5"
                    label={{
                      value: `Safety Stock (${atpData.safety_stock})`,
                      position: 'right',
                      fill: '#ef4444',
                      fontSize: 11,
                    }}
                  />
                )}
              </AreaChart>
            </ResponsiveContainer>
          </div>

          {/* Risk Alert */}
          {isAtRisk && (
            <Alert variant="warning">
              <WarningIcon className="h-4 w-4" />
              <AlertTitle>Stockout Risk Detected</AlertTitle>
              <AlertDescription>
                <p className="text-xs">
                  The P10 (pessimistic) ATP of <strong>{atpData.atp_p10}</strong> units
                  is below the safety stock threshold.
                  There is approximately 10% probability that actual ATP could be this low or lower.
                </p>
                <p className="text-xs mt-2">
                  Consider expediting replenishment orders or increasing safety stock to mitigate risk.
                </p>
              </AlertDescription>
            </Alert>
          )}

          {/* Legend */}
          <div className="p-3 bg-muted/50 rounded-lg">
            <p className="text-xs text-muted-foreground">
              <strong className="text-emerald-600">P90</strong>: 90% probability ATP will be at or below this value (optimistic scenario)
              <br />
              <strong className="text-primary">Median</strong>: Most likely ATP (50th percentile of the predictive distribution)
              <br />
              <strong className="text-amber-600">P10</strong>: 10% probability ATP will be at or below this value (pessimistic scenario)
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
};

export default ProbabilisticATPChart;
