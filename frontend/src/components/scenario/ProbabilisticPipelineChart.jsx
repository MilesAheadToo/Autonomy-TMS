/**
 * Probabilistic Pipeline Chart Component
 *
 * Phase 5: Stochastic Lead Times Integration
 * Displays pipeline shipments with probabilistic arrival windows based on
 * Monte Carlo simulation of stochastic lead times.
 *
 * Shows P10/P50/P90 arrival round estimates for each shipment.
 *
 * Props:
 * - gameId: Game ID
 * - scenarioUserId: ScenarioUser ID
 * - currentRound: Current round number
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
  Progress,
} from '../common';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  Cell,
  ReferenceLine,
} from 'recharts';
import {
  Truck as ShipIcon,
  AlertTriangle as WarningIcon,
  CheckCircle2 as CheckIcon,
  Info as InfoIcon,
  RefreshCw,
  Clock as ScheduleIcon,
} from 'lucide-react';
import simulationApi from '../../services/api';
import { cn } from '@azirella-ltd/autonomy-frontend';

const ProbabilisticPipelineChart = ({
  gameId,
  scenarioUserId,
  currentRound: propCurrentRound,
  nSimulations = 100,
}) => {
  const [pipelineData, setPipelineData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetchPipelineVisualization();
  }, [gameId, scenarioUserId, nSimulations]);

  const fetchPipelineVisualization = async () => {
    if (!gameId || !scenarioUserId) return;

    setLoading(true);
    setError(null);

    try {
      const data = await simulationApi.getPipelineVisualization(
        gameId,
        scenarioUserId,
        nSimulations
      );
      setPipelineData(data);
    } catch (err) {
      console.error('Error fetching pipeline visualization:', err);
      setError(err.response?.data?.detail || 'Failed to fetch pipeline data');
    } finally {
      setLoading(false);
    }
  };

  // Custom tooltip
  const CustomTooltip = ({ active, payload }) => {
    if (!active || !payload || payload.length === 0) {
      return null;
    }

    const data = payload[0].payload;

    return (
      <Card className="p-2 bg-white/95 dark:bg-card/95 shadow-lg border">
        <CardContent className="p-2">
          <h4 className="font-semibold text-sm mb-2">
            Shipment (Slot {data.slot})
          </h4>
          <div className="space-y-1.5 text-xs">
            <div className="flex justify-between gap-4">
              <span className="text-muted-foreground">Quantity:</span>
              <span className="font-semibold">{data.quantity} units</span>
            </div>
            <div className="flex justify-between gap-4">
              <span className="text-muted-foreground">Scheduled Arrival:</span>
              <span className="font-semibold">Round {data.scheduled_arrival_round}</span>
            </div>
            <div className="flex justify-between gap-4 pt-1.5 border-t border-border">
              <span className="text-muted-foreground">P10 Arrival:</span>
              <span className="font-semibold text-amber-600">Round {data.arrival_p10_round}</span>
            </div>
            <div className="flex justify-between gap-4">
              <span className="text-muted-foreground">Median Arrival:</span>
              <span className="font-semibold text-primary">Round {data.arrival_p50_round}</span>
            </div>
            <div className="flex justify-between gap-4">
              <span className="text-muted-foreground">P90 Arrival:</span>
              <span className="font-semibold text-emerald-600">Round {data.arrival_p90_round}</span>
            </div>
            <div className="flex justify-between gap-4 pt-1.5 border-t border-border">
              <span className="text-muted-foreground">Prob. Next Round:</span>
              <span className="font-semibold">
                {(data.arrival_probability_next_round * 100).toFixed(0)}%
              </span>
            </div>
            {data.source_node && (
              <div className="flex justify-between gap-4">
                <span className="text-muted-foreground">Source:</span>
                <span className="font-semibold">{data.source_node}</span>
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

  if (!pipelineData || pipelineData.message) {
    return (
      <Alert variant="info">
        <InfoIcon className="h-4 w-4" />
        <AlertDescription>
          {pipelineData?.message || 'No pipeline data available'}
        </AlertDescription>
      </Alert>
    );
  }

  const { shipments, arrival_distribution, lead_time_stats } = pipelineData;
  const currentRound = pipelineData.current_period || propCurrentRound;

  // Build chart data for arrival distribution
  const arrivalChartData = Object.entries(arrival_distribution || {})
    .map(([roundKey, stats]) => ({
      round: parseInt(roundKey.replace('round_', '')),
      roundLabel: `R${roundKey.replace('round_', '')}`,
      p10: stats.quantity_p10,
      p50: stats.quantity_p50,
      p90: stats.quantity_p90,
    }))
    .sort((a, b) => a.round - b.round);

  return (
    <Card className="mb-4">
      <CardContent className="pt-4">
        <div className="space-y-4">
          {/* Header */}
          <div className="flex justify-between items-center">
            <h3 className="text-lg font-semibold flex items-center gap-2">
              <ShipIcon className="h-5 w-5 text-primary" />
              Probabilistic Pipeline Visualization
            </h3>
            <div className="flex gap-2 items-center">
              <Badge variant="outline">
                {pipelineData.pipeline_total} units in transit
              </Badge>
              <Badge variant="secondary" className="text-xs">
                {nSimulations} simulations
              </Badge>
              <Button
                variant="ghost"
                size="sm"
                onClick={fetchPipelineVisualization}
                className="h-8 w-8 p-0"
              >
                <RefreshCw className="h-4 w-4" />
              </Button>
            </div>
          </div>

          {/* Lead Time Stats */}
          <div className="grid grid-cols-5 gap-2">
            <Card className="bg-muted/50">
              <CardContent className="pt-3 pb-2 text-center">
                <p className="text-xs text-muted-foreground">Mean LT</p>
                <p className="text-lg font-bold">
                  {lead_time_stats?.mean?.toFixed(1) || 'N/A'}
                </p>
              </CardContent>
            </Card>
            <Card className="bg-muted/50">
              <CardContent className="pt-3 pb-2 text-center">
                <p className="text-xs text-muted-foreground">Std Dev</p>
                <p className="text-lg font-bold">
                  {lead_time_stats?.stddev?.toFixed(2) || 'N/A'}
                </p>
              </CardContent>
            </Card>
            <Card className="bg-amber-50 dark:bg-amber-950/30">
              <CardContent className="pt-3 pb-2 text-center">
                <p className="text-xs text-amber-600">P10</p>
                <p className="text-lg font-bold text-amber-700 dark:text-amber-300">
                  {lead_time_stats?.p10 || 'N/A'}
                </p>
              </CardContent>
            </Card>
            <Card className="bg-primary/10">
              <CardContent className="pt-3 pb-2 text-center">
                <p className="text-xs text-primary">Median</p>
                <p className="text-lg font-bold text-primary">
                  {lead_time_stats?.p50 || 'N/A'}
                </p>
              </CardContent>
            </Card>
            <Card className="bg-emerald-50 dark:bg-emerald-950/30">
              <CardContent className="pt-3 pb-2 text-center">
                <p className="text-xs text-emerald-600">P90</p>
                <p className="text-lg font-bold text-emerald-700 dark:text-emerald-300">
                  {lead_time_stats?.p90 || 'N/A'}
                </p>
              </CardContent>
            </Card>
          </div>

          {/* Shipments List with Probability Windows */}
          {shipments && shipments.length > 0 && (
            <div className="space-y-2">
              <h4 className="text-sm font-semibold">Shipments with Arrival Windows</h4>
              {shipments.map((shipment, idx) => (
                <div
                  key={idx}
                  className="flex items-center gap-4 p-3 bg-muted/30 rounded-lg"
                >
                  {/* Shipment Info */}
                  <div className="min-w-[100px]">
                    <Badge variant="outline" className="mb-1">
                      {shipment.quantity} units
                    </Badge>
                    {shipment.source_node && (
                      <p className="text-xs text-muted-foreground">
                        from {shipment.source_node}
                      </p>
                    )}
                  </div>

                  {/* Arrival Window Visualization */}
                  <div className="flex-1">
                    <div className="relative h-8 bg-muted rounded-full overflow-hidden">
                      {/* P10-P90 range bar */}
                      <div
                        className="absolute h-full bg-gradient-to-r from-amber-300 via-blue-400 to-emerald-300 opacity-50"
                        style={{
                          left: `${((shipment.arrival_p10_round - currentRound) / 5) * 100}%`,
                          width: `${((shipment.arrival_p90_round - shipment.arrival_p10_round + 1) / 5) * 100}%`,
                        }}
                      />
                      {/* P50 marker */}
                      <div
                        className="absolute w-1 h-full bg-primary"
                        style={{
                          left: `${((shipment.arrival_p50_round - currentRound + 0.5) / 5) * 100}%`,
                        }}
                      />
                    </div>
                    <div className="flex justify-between text-xs text-muted-foreground mt-1">
                      <span>R{shipment.arrival_p10_round}</span>
                      <span className="text-primary font-semibold">
                        R{shipment.arrival_p50_round}
                      </span>
                      <span>R{shipment.arrival_p90_round}</span>
                    </div>
                  </div>

                  {/* Probability for Next Round */}
                  <div className="min-w-[100px] text-right">
                    <div className="text-sm font-semibold">
                      {(shipment.arrival_probability_next_round * 100).toFixed(0)}%
                    </div>
                    <p className="text-xs text-muted-foreground">
                      prob. next round
                    </p>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Arrival Distribution Chart */}
          {arrivalChartData.length > 0 && (
            <div className="mt-4">
              <h4 className="text-sm font-semibold mb-2">
                Expected Arrivals by Round
              </h4>
              <div className="w-full h-[200px]">
                <ResponsiveContainer>
                  <BarChart
                    data={arrivalChartData}
                    margin={{ top: 20, right: 30, left: 20, bottom: 5 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                    <XAxis dataKey="roundLabel" tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 11 }} />
                    <Tooltip />
                    <Legend wrapperStyle={{ fontSize: '11px' }} />

                    {/* Current round marker */}
                    <ReferenceLine
                      x={`R${currentRound}`}
                      stroke="#ef4444"
                      strokeWidth={2}
                      label={{
                        value: 'Now',
                        position: 'top',
                        fill: '#ef4444',
                        fontSize: 10,
                      }}
                    />

                    <Bar
                      dataKey="p10"
                      name="P10 (Pessimistic)"
                      fill="#f59e0b"
                      opacity={0.6}
                    />
                    <Bar
                      dataKey="p50"
                      name="Median"
                      fill="#3b82f6"
                    />
                    <Bar
                      dataKey="p90"
                      name="P90 (Optimistic)"
                      fill="#22c55e"
                      opacity={0.6}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {/* Empty state */}
          {(!shipments || shipments.length === 0) && (
            <Alert variant="info">
              <InfoIcon className="h-4 w-4" />
              <AlertDescription>
                No shipments currently in transit. Place orders to see pipeline visualization.
              </AlertDescription>
            </Alert>
          )}

          {/* Legend */}
          <div className="p-3 bg-muted/50 rounded-lg">
            <p className="text-xs text-muted-foreground">
              <strong className="text-amber-600">P10</strong>: 10% probability arrival will be at or before this round (earliest)
              <br />
              <strong className="text-primary">Median</strong>: Most likely arrival round (50th percentile)
              <br />
              <strong className="text-emerald-600">P90</strong>: 90% probability arrival will be at or before this round (latest)
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
};

export default ProbabilisticPipelineChart;
