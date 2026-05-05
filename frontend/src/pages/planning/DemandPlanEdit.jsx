/**
 * Demand Plan Edit Page
 *
 * Full-featured demand planning page with:
 * - Editable forecast table
 * - ML forecast pipeline
 * - Adjustment history with filtering
 * - Version comparison side-by-side
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useDisplayPreferences } from '../../contexts/DisplayPreferencesContext';
import {
  Card,
  CardContent,
  Button,
  Alert,
  Badge,
  Label,
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
  Spinner,
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
  Input,
  Modal,
} from '../../components/common';
import {
  Pencil,
  History,
  ArrowLeftRight,
  Upload,
  Download,
  RefreshCw,
  Undo2,
  Calendar,
  User,
  Filter,
  TrendingUp,
  TrendingDown,
  Minus,
  ChevronLeft,
  ChevronRight,
  Save,
  Lock,
} from 'lucide-react';
import { ForecastEditor, ForecastPipelineManager } from '../../components/demand-planning';
import { getSupplyChainConfigs } from '../../services/supplyChainConfigService';
import { api } from '../../services/api';

// Reason code labels and colors
const REASON_LABELS = {
  promotion: { label: 'Promotion', color: 'text-purple-600 bg-purple-50' },
  seasonal: { label: 'Seasonal', color: 'text-blue-600 bg-blue-50' },
  event: { label: 'Event', color: 'text-amber-600 bg-amber-50' },
  market_intelligence: { label: 'Market Intel', color: 'text-green-600 bg-green-50' },
  correction: { label: 'Correction', color: 'text-red-600 bg-red-50' },
  other: { label: 'Other', color: 'text-gray-600 bg-gray-50' },
};

const VERSION_TYPE_LABELS = {
  snapshot: { label: 'Snapshot', color: 'secondary' },
  baseline: { label: 'Baseline', color: 'default' },
  consensus: { label: 'Consensus', color: 'info' },
  published: { label: 'Published', color: 'success' },
};

// ============================================================================
// Adjustment History Tab
// ============================================================================
const AdjustmentHistoryTab = ({ configId }) => {
  const [adjustments, setAdjustments] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [page, setPage] = useState(0);
  const [rowsPerPage] = useState(20);
  const [filters, setFilters] = useState({
    reason_code: '__all__',
    source: '__all__',
    status: '__all__',
  });
  const [revertingId, setRevertingId] = useState(null);

  const loadAdjustments = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (configId) params.append('config_id', configId);
      params.append('limit', '200');
      if (filters.reason_code !== '__all__') params.append('reason_code', filters.reason_code);
      if (filters.source !== '__all__') params.append('source', filters.source);
      if (filters.status !== '__all__') params.append('status', filters.status);

      const response = await api.get(`/v1/forecast-adjustments/table?${params.toString()}`);
      // Fallback: if the endpoint returns the table data, we use the /history approach
      // The adjustments list endpoint returns all adjustments
      const historyResponse = await api.get(`/v1/forecast-adjustments?limit=200`);
      setAdjustments(historyResponse.data || []);
    } catch (err) {
      console.error('Failed to load adjustment history:', err);
      setAdjustments([]);
    } finally {
      setLoading(false);
    }
  }, [configId, filters]);

  useEffect(() => {
    loadAdjustments();
  }, [loadAdjustments]);

  const handleRevert = async (adjustmentId) => {
    setRevertingId(adjustmentId);
    try {
      await api.delete(`/v1/forecast-adjustments/${adjustmentId}`);
      setAdjustments(prev =>
        prev.map(a => a.id === adjustmentId ? { ...a, status: 'reverted' } : a)
      );
    } catch (err) {
      console.error('Failed to revert adjustment:', err);
    } finally {
      setRevertingId(null);
    }
  };

  const filteredAdjustments = adjustments.filter(a => {
    if (filters.reason_code !== '__all__' && a.reason_code !== filters.reason_code) return false;
    if (filters.source !== '__all__' && a.source !== filters.source) return false;
    if (filters.status !== '__all__' && a.status !== filters.status) return false;
    return true;
  });

  const paginatedAdjustments = filteredAdjustments.slice(
    page * rowsPerPage,
    (page + 1) * rowsPerPage
  );

  const totalPages = Math.ceil(filteredAdjustments.length / rowsPerPage);

  // Summary stats
  const stats = {
    total: filteredAdjustments.length,
    applied: filteredAdjustments.filter(a => a.status === 'applied').length,
    reverted: filteredAdjustments.filter(a => a.status === 'reverted').length,
    pending: filteredAdjustments.filter(a => a.status === 'pending_approval').length,
  };

  return (
    <div className="space-y-4">
      {/* Summary Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card>
          <CardContent className="pt-4">
            <p className="text-sm text-muted-foreground">Total Adjustments</p>
            <p className="text-2xl font-bold">{stats.total}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <p className="text-sm text-muted-foreground">Applied</p>
            <p className="text-2xl font-bold text-green-600">{stats.applied}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <p className="text-sm text-muted-foreground">Reverted</p>
            <p className="text-2xl font-bold text-red-600">{stats.reverted}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <p className="text-sm text-muted-foreground">Pending Approval</p>
            <p className="text-2xl font-bold text-amber-600">{stats.pending}</p>
          </CardContent>
        </Card>
      </div>

      {/* Filters */}
      <Card>
        <CardContent className="pt-4">
          <div className="flex items-center gap-4 flex-wrap">
            <div className="flex items-center gap-2">
              <Filter className="h-4 w-4 text-muted-foreground" />
              <span className="text-sm font-medium">Filters:</span>
            </div>
            <div className="flex-1 grid grid-cols-1 md:grid-cols-3 gap-3">
              <Select
                value={filters.reason_code}
                onValueChange={(v) => setFilters(prev => ({ ...prev, reason_code: v }))}
              >
                <SelectTrigger>
                  <SelectValue placeholder="All Reasons" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">All Reasons</SelectItem>
                  {Object.entries(REASON_LABELS).map(([key, { label }]) => (
                    <SelectItem key={key} value={key}>{label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <Select
                value={filters.source}
                onValueChange={(v) => setFilters(prev => ({ ...prev, source: v }))}
              >
                <SelectTrigger>
                  <SelectValue placeholder="All Sources" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">All Sources</SelectItem>
                  <SelectItem value="manual">Manual</SelectItem>
                  <SelectItem value="bulk">Bulk</SelectItem>
                  <SelectItem value="agent">Agent</SelectItem>
                  <SelectItem value="import">Import</SelectItem>
                </SelectContent>
              </Select>

              <Select
                value={filters.status}
                onValueChange={(v) => setFilters(prev => ({ ...prev, status: v }))}
              >
                <SelectTrigger>
                  <SelectValue placeholder="All Statuses" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">All Statuses</SelectItem>
                  <SelectItem value="applied">Applied</SelectItem>
                  <SelectItem value="pending_approval">Pending</SelectItem>
                  <SelectItem value="reverted">Reverted</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={loadAdjustments}
              leftIcon={<RefreshCw className="h-4 w-4" />}
            >
              Refresh
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Adjustments Table */}
      <Card>
        <CardContent className="pt-4">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <Spinner size="lg" />
              <span className="ml-3 text-muted-foreground">Loading adjustment history...</span>
            </div>
          ) : error ? (
            <Alert variant="error">{error}</Alert>
          ) : paginatedAdjustments.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground">
              <History className="h-12 w-12 mx-auto mb-3 opacity-50" />
              <p className="text-lg font-medium">No adjustments found</p>
              <p className="text-sm">Make forecast edits in the Edit tab to see history here.</p>
            </div>
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Date</TableHead>
                    <TableHead>Product</TableHead>
                    <TableHead>Site</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead className="text-right">Original</TableHead>
                    <TableHead className="text-right">Change</TableHead>
                    <TableHead className="text-right">New Value</TableHead>
                    <TableHead>Reason</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead>User</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {paginatedAdjustments.map((adj) => {
                    const changeValue = adj.new_value - adj.original_value;
                    const changePercent = adj.original_value
                      ? ((changeValue / adj.original_value) * 100).toFixed(1)
                      : '0';
                    const isIncrease = changeValue > 0;
                    const reasonInfo = REASON_LABELS[adj.reason_code] || REASON_LABELS.other;

                    return (
                      <TableRow key={adj.id} className={adj.status === 'reverted' ? 'opacity-50' : ''}>
                        <TableCell className="text-sm">
                          <div className="flex items-center gap-1">
                            <Calendar className="h-3 w-3 text-muted-foreground" />
                            {new Date(adj.created_at).toLocaleDateString()}
                          </div>
                          <span className="text-xs text-muted-foreground">
                            {new Date(adj.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                          </span>
                        </TableCell>
                        <TableCell className="font-medium text-sm">
                          {adj.product_name || `Product ${adj.forecast_id}`}
                        </TableCell>
                        <TableCell className="text-sm">
                          {adj.site_name || '-'}
                        </TableCell>
                        <TableCell>
                          <Badge variant="outline" className="text-xs">
                            {adj.adjustment_type}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-right font-mono text-sm">
                          {adj.original_value?.toLocaleString() ?? '-'}
                        </TableCell>
                        <TableCell className="text-right">
                          <span className={`flex items-center justify-end gap-1 text-sm font-medium ${
                            isIncrease ? 'text-green-600' : 'text-red-600'
                          }`}>
                            {isIncrease ? (
                              <TrendingUp className="h-3 w-3" />
                            ) : changeValue < 0 ? (
                              <TrendingDown className="h-3 w-3" />
                            ) : (
                              <Minus className="h-3 w-3" />
                            )}
                            {isIncrease ? '+' : ''}{changeValue?.toLocaleString()}
                            <span className="text-xs text-muted-foreground">
                              ({isIncrease ? '+' : ''}{changePercent}%)
                            </span>
                          </span>
                        </TableCell>
                        <TableCell className="text-right font-mono text-sm font-medium">
                          {adj.new_value?.toLocaleString() ?? '-'}
                        </TableCell>
                        <TableCell>
                          {adj.reason_code && (
                            <span className={`text-xs px-2 py-0.5 rounded-full ${reasonInfo.color}`}>
                              {reasonInfo.label}
                            </span>
                          )}
                          {adj.reason_text && (
                            <p className="text-xs text-muted-foreground mt-0.5 max-w-[120px] truncate" title={adj.reason_text}>
                              {adj.reason_text}
                            </p>
                          )}
                        </TableCell>
                        <TableCell>
                          <Badge variant="outline" className="text-xs capitalize">
                            {adj.source}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-sm">
                          <div className="flex items-center gap-1">
                            <User className="h-3 w-3 text-muted-foreground" />
                            {adj.created_by_name || `User ${adj.created_by_id}`}
                          </div>
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant={
                              adj.status === 'applied' ? 'success' :
                              adj.status === 'reverted' ? 'destructive' :
                              'warning'
                            }
                            className="text-xs"
                          >
                            {adj.status}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-right">
                          {adj.status === 'applied' && (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => handleRevert(adj.id)}
                              disabled={revertingId === adj.id}
                              leftIcon={<Undo2 className="h-3 w-3" />}
                            >
                              {revertingId === adj.id ? 'Reverting...' : 'Revert'}
                            </Button>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>

              {/* Pagination */}
              {totalPages > 1 && (
                <div className="flex items-center justify-between mt-4 pt-4 border-t">
                  <span className="text-sm text-muted-foreground">
                    Showing {page * rowsPerPage + 1}-{Math.min((page + 1) * rowsPerPage, filteredAdjustments.length)} of {filteredAdjustments.length}
                  </span>
                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setPage(p => Math.max(0, p - 1))}
                      disabled={page === 0}
                      leftIcon={<ChevronLeft className="h-4 w-4" />}
                    >
                      Previous
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
                      disabled={page >= totalPages - 1}
                      leftIcon={<ChevronRight className="h-4 w-4" />}
                    >
                      Next
                    </Button>
                  </div>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
};

// ============================================================================
// Version Comparison Tab
// ============================================================================
const VersionComparisonTab = ({ configId }) => {
  const [versions, setVersions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [versionA, setVersionA] = useState(undefined);
  const [versionB, setVersionB] = useState(undefined);
  const [comparison, setComparison] = useState(null);
  const [comparing, setComparing] = useState(false);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [newVersion, setNewVersion] = useState({
    version_name: '',
    version_type: 'snapshot',
    notes: '',
  });
  const [creating, setCreating] = useState(false);

  const loadVersions = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (configId) params.append('config_id', configId);
      params.append('limit', '50');
      const response = await api.get(`/v1/forecast-adjustments/versions?${params.toString()}`);
      setVersions(response.data || []);
    } catch (err) {
      console.error('Failed to load versions:', err);
      setVersions([]);
    } finally {
      setLoading(false);
    }
  }, [configId]);

  useEffect(() => {
    loadVersions();
  }, [loadVersions]);

  const handleCompare = async () => {
    if (!versionA || !versionB || versionA === versionB) return;
    setComparing(true);
    try {
      const response = await api.get(
        `/v1/forecast-adjustments/versions/compare?version_a=${versionA}&version_b=${versionB}`
      );
      setComparison(response.data);
    } catch (err) {
      console.error('Failed to compare versions:', err);
      setComparison(null);
    } finally {
      setComparing(false);
    }
  };

  const handleCreateVersion = async () => {
    setCreating(true);
    try {
      const now = new Date();
      const periodEnd = new Date(now);
      periodEnd.setMonth(periodEnd.getMonth() + 3);

      await api.post('/forecast-adjustments/versions', {
        ...newVersion,
        config_id: configId ? parseInt(configId) : null,
        period_start: now.toISOString(),
        period_end: periodEnd.toISOString(),
      });
      setCreateDialogOpen(false);
      setNewVersion({ version_name: '', version_type: 'snapshot', notes: '' });
      loadVersions();
    } catch (err) {
      console.error('Failed to create version:', err);
    } finally {
      setCreating(false);
    }
  };

  const versionAData = versions.find(v => v.id?.toString() === versionA);
  const versionBData = versions.find(v => v.id?.toString() === versionB);

  return (
    <div className="space-y-4">
      {/* Version Selection */}
      <Card>
        <CardContent className="pt-4">
          <div className="flex items-end gap-4">
            <div className="flex-1">
              <Label>Version A (Baseline)</Label>
              <Select value={versionA} onValueChange={setVersionA}>
                <SelectTrigger className="mt-1">
                  <SelectValue placeholder="Select version..." />
                </SelectTrigger>
                <SelectContent>
                  {versions.map(v => (
                    <SelectItem key={v.id} value={v.id.toString()}>
                      {v.version_name || `Version ${v.version_number}`}
                      {' '}({v.version_type}) - {new Date(v.created_at).toLocaleDateString()}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-center pb-2">
              <ArrowLeftRight className="h-5 w-5 text-muted-foreground" />
            </div>
            <div className="flex-1">
              <Label>Version B (Compare)</Label>
              <Select value={versionB} onValueChange={setVersionB}>
                <SelectTrigger className="mt-1">
                  <SelectValue placeholder="Select version..." />
                </SelectTrigger>
                <SelectContent>
                  {versions.map(v => (
                    <SelectItem key={v.id} value={v.id.toString()}>
                      {v.version_name || `Version ${v.version_number}`}
                      {' '}({v.version_type}) - {new Date(v.created_at).toLocaleDateString()}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button
              onClick={handleCompare}
              disabled={!versionA || !versionB || versionA === versionB || comparing}
              leftIcon={comparing ? <Spinner size="sm" /> : <ArrowLeftRight className="h-4 w-4" />}
            >
              {comparing ? 'Comparing...' : 'Compare'}
            </Button>
            <Button
              variant="outline"
              onClick={() => setCreateDialogOpen(true)}
              leftIcon={<Save className="h-4 w-4" />}
            >
              Create Snapshot
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Version List */}
      {!comparison && (
        <Card>
          <CardContent className="pt-4">
            <h3 className="text-lg font-medium mb-4">Saved Versions</h3>
            {loading ? (
              <div className="flex items-center justify-center py-8">
                <Spinner size="lg" />
              </div>
            ) : versions.length === 0 ? (
              <div className="text-center py-8 text-muted-foreground">
                <Save className="h-12 w-12 mx-auto mb-3 opacity-50" />
                <p className="text-lg font-medium">No versions saved yet</p>
                <p className="text-sm">Create a snapshot to capture the current forecast state.</p>
                <Button className="mt-4" onClick={() => setCreateDialogOpen(true)}>
                  Create First Snapshot
                </Button>
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>#</TableHead>
                    <TableHead>Name</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Current</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Created</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {versions.map(v => {
                    const typeInfo = VERSION_TYPE_LABELS[v.version_type] || VERSION_TYPE_LABELS.snapshot;
                    return (
                      <TableRow key={v.id}>
                        <TableCell className="font-mono text-sm">{v.version_number}</TableCell>
                        <TableCell className="font-medium">{v.version_name || `Version ${v.version_number}`}</TableCell>
                        <TableCell>
                          <Badge variant={typeInfo.color} className="text-xs">{typeInfo.label}</Badge>
                        </TableCell>
                        <TableCell>
                          {v.is_current && <Badge variant="success" className="text-xs">Current</Badge>}
                        </TableCell>
                        <TableCell>
                          {v.is_locked ? (
                            <span className="flex items-center gap-1 text-sm text-muted-foreground">
                              <Lock className="h-3 w-3" /> Locked
                            </span>
                          ) : (
                            <span className="text-sm text-green-600">Open</span>
                          )}
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground">
                          {new Date(v.created_at).toLocaleDateString()}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}

      {/* Comparison Results */}
      {comparison && (
        <Card>
          <CardContent className="pt-4">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-medium">
                Comparison: {versionAData?.version_name || `Version ${versionAData?.version_number}`}
                {' vs '}
                {versionBData?.version_name || `Version ${versionBData?.version_number}`}
              </h3>
              <Button variant="outline" size="sm" onClick={() => setComparison(null)}>
                Clear
              </Button>
            </div>

            {/* Delta Summary */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
              <Card className="bg-muted/30">
                <CardContent className="pt-3 pb-3">
                  <p className="text-xs text-muted-foreground">Products Changed</p>
                  <p className="text-xl font-bold">{comparison.products_changed ?? comparison.deltas?.length ?? 0}</p>
                </CardContent>
              </Card>
              <Card className="bg-muted/30">
                <CardContent className="pt-3 pb-3">
                  <p className="text-xs text-muted-foreground">Avg Change</p>
                  <p className="text-xl font-bold">
                    {comparison.avg_change != null
                      ? `${comparison.avg_change > 0 ? '+' : ''}${comparison.avg_change.toFixed(1)}%`
                      : '-'}
                  </p>
                </CardContent>
              </Card>
              <Card className="bg-muted/30">
                <CardContent className="pt-3 pb-3">
                  <p className="text-xs text-muted-foreground">Increases</p>
                  <p className="text-xl font-bold text-green-600">{comparison.increases ?? 0}</p>
                </CardContent>
              </Card>
              <Card className="bg-muted/30">
                <CardContent className="pt-3 pb-3">
                  <p className="text-xs text-muted-foreground">Decreases</p>
                  <p className="text-xl font-bold text-red-600">{comparison.decreases ?? 0}</p>
                </CardContent>
              </Card>
            </div>

            {/* Detailed Delta Table */}
            {comparison.deltas && comparison.deltas.length > 0 && (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Product</TableHead>
                    <TableHead>Site</TableHead>
                    <TableHead>Period</TableHead>
                    <TableHead className="text-right">Version A</TableHead>
                    <TableHead className="text-right">Version B</TableHead>
                    <TableHead className="text-right">Delta</TableHead>
                    <TableHead className="text-right">% Change</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {comparison.deltas.map((delta, idx) => {
                    const change = delta.value_b - delta.value_a;
                    const pctChange = delta.value_a ? ((change / delta.value_a) * 100).toFixed(1) : '0';
                    const isUp = change > 0;
                    return (
                      <TableRow key={idx}>
                        <TableCell className="font-medium">{delta.product_name || delta.product_id}</TableCell>
                        <TableCell>{delta.site_name || delta.site_id}</TableCell>
                        <TableCell className="font-mono text-sm">{delta.period}</TableCell>
                        <TableCell className="text-right font-mono">{delta.value_a?.toLocaleString()}</TableCell>
                        <TableCell className="text-right font-mono">{delta.value_b?.toLocaleString()}</TableCell>
                        <TableCell className={`text-right font-mono font-medium ${isUp ? 'text-green-600' : change < 0 ? 'text-red-600' : ''}`}>
                          {isUp ? '+' : ''}{change?.toLocaleString()}
                        </TableCell>
                        <TableCell className={`text-right font-mono ${isUp ? 'text-green-600' : change < 0 ? 'text-red-600' : ''}`}>
                          {isUp ? '+' : ''}{pctChange}%
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}

      {/* Create Version Dialog */}
      <Modal
        open={createDialogOpen}
        onClose={() => setCreateDialogOpen(false)}
        title="Create Forecast Snapshot"
      >
        <div className="space-y-4 p-4">
          <div>
            <Label>Version Name</Label>
            <Input
              className="mt-1"
              placeholder="e.g., Q2 2026 Baseline"
              value={newVersion.version_name}
              onChange={(e) => setNewVersion(prev => ({ ...prev, version_name: e.target.value }))}
            />
          </div>
          <div>
            <Label>Version Type</Label>
            <Select
              value={newVersion.version_type}
              onValueChange={(v) => setNewVersion(prev => ({ ...prev, version_type: v }))}
            >
              <SelectTrigger className="mt-1">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="snapshot">Snapshot</SelectItem>
                <SelectItem value="baseline">Baseline</SelectItem>
                <SelectItem value="consensus">Consensus</SelectItem>
                <SelectItem value="published">Published</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label>Notes (optional)</Label>
            <Input
              className="mt-1"
              placeholder="Reason for snapshot..."
              value={newVersion.notes}
              onChange={(e) => setNewVersion(prev => ({ ...prev, notes: e.target.value }))}
            />
          </div>
          <div className="flex justify-end gap-2 pt-4">
            <Button variant="outline" onClick={() => setCreateDialogOpen(false)}>Cancel</Button>
            <Button
              onClick={handleCreateVersion}
              disabled={creating}
              leftIcon={creating ? <Spinner size="sm" /> : <Save className="h-4 w-4" />}
            >
              {creating ? 'Creating...' : 'Create Snapshot'}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
};

// ============================================================================
// Mock data generators (used when API endpoints are not available)
// ============================================================================
function generateMockAdjustments() {
  const reasons = ['promotion', 'seasonal', 'event', 'market_intelligence', 'correction'];
  const sources = ['manual', 'bulk', 'agent'];
  const statuses = ['applied', 'applied', 'applied', 'reverted', 'pending_approval'];
  const products = ['Lager 6-Pack', 'IPA Case', 'Stout Keg', 'Pilsner Pallet', 'Wheat Ale 12-Pack'];
  const sites = ['DC-East', 'DC-West', 'DC-Central', 'Plant-North', 'Plant-South'];

  return Array.from({ length: 24 }, (_, i) => {
    const original = Math.floor(Math.random() * 1000) + 200;
    const changePercent = (Math.random() * 40 - 20);
    const newValue = Math.round(original * (1 + changePercent / 100));
    return {
      id: i + 1,
      forecast_id: Math.floor(Math.random() * 100) + 1,
      product_name: products[i % products.length],
      site_name: sites[i % sites.length],
      adjustment_type: changePercent > 0 ? 'delta' : 'percentage',
      original_value: original,
      adjustment_value: newValue - original,
      new_value: newValue,
      reason_code: reasons[i % reasons.length],
      reason_text: i % 3 === 0 ? 'Adjusted based on latest market signals' : null,
      source: sources[i % sources.length],
      status: statuses[i % statuses.length],
      created_by_id: 1,
      created_by_name: 'Trevor',
      created_at: new Date(Date.now() - i * 3600000 * 6).toISOString(),
    };
  });
}

function generateMockVersions() {
  return [
    { id: 1, version_number: 1, version_name: 'January Baseline', version_type: 'baseline', is_current: false, is_locked: true, created_at: '2026-01-15T10:00:00Z' },
    { id: 2, version_number: 2, version_name: 'February Baseline', version_type: 'baseline', is_current: false, is_locked: true, created_at: '2026-02-01T10:00:00Z' },
    { id: 3, version_number: 3, version_name: 'Q1 Consensus', version_type: 'consensus', is_current: false, is_locked: true, created_at: '2026-02-15T10:00:00Z' },
    { id: 4, version_number: 4, version_name: 'March Working', version_type: 'snapshot', is_current: true, is_locked: false, created_at: '2026-03-01T10:00:00Z' },
  ];
}

function generateMockComparison(vA, vB) {
  const products = ['Lager 6-Pack', 'IPA Case', 'Stout Keg', 'Pilsner Pallet'];
  const sites = ['DC-East', 'DC-West'];
  const periods = ['2026-W10', '2026-W11', '2026-W12'];
  const deltas = [];
  let increases = 0, decreases = 0, totalChange = 0;

  for (const product of products) {
    for (const site of sites) {
      for (const period of periods) {
        if (Math.random() > 0.5) {
          const valueA = Math.floor(Math.random() * 500) + 100;
          const changePct = (Math.random() * 30 - 15);
          const valueB = Math.round(valueA * (1 + changePct / 100));
          deltas.push({
            product_name: product,
            site_name: site,
            period,
            value_a: valueA,
            value_b: valueB,
          });
          if (valueB > valueA) increases++;
          else if (valueB < valueA) decreases++;
          totalChange += changePct;
        }
      }
    }
  }

  return {
    products_changed: new Set(deltas.map(d => d.product_name)).size,
    avg_change: deltas.length > 0 ? totalChange / deltas.length : 0,
    increases,
    decreases,
    deltas,
  };
}

// ============================================================================
// Main Component
// ============================================================================
const DemandPlanEdit = () => {
  const { formatProduct, formatSite, loadLookupsForConfig } = useDisplayPreferences();
  const [activeTab, setActiveTab] = useState('edit');
  const [selectedConfig, setSelectedConfig] = useState(undefined);
  const [timeGranularity, setTimeGranularity] = useState('week');
  const fileInputRef = useRef(null);
  const [importStatus, setImportStatus] = useState(null);

  // Supply chain configs loaded from API (filtered by user's tenant)
  const [supplyChainConfigs, setSupplyChainConfigs] = useState([]);
  const [configsLoading, setConfigsLoading] = useState(true);
  const [configsError, setConfigsError] = useState(null);

  // Load supply chain configs on mount and auto-select user's default
  useEffect(() => {
    const loadConfigs = async () => {
      setConfigsLoading(true);
      setConfigsError(null);
      try {
        const configs = await getSupplyChainConfigs();
        setSupplyChainConfigs(configs);
        // Auto-select the first config if none selected
        if (!selectedConfig && configs.length > 0) {
          setSelectedConfig(configs[0].id.toString());
        }
      } catch (err) {
        console.error('Failed to load supply chain configs:', err);
        setConfigsError('Failed to load supply chain configurations');
      } finally {
        setConfigsLoading(false);
      }
    };
    loadConfigs();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Load display name lookups when config changes
  useEffect(() => {
    if (selectedConfig) loadLookupsForConfig(parseInt(selectedConfig));
  }, [selectedConfig, loadLookupsForConfig]);

  // Export forecasts as CSV
  const handleExport = async () => {
    try {
      const params = {};
      if (selectedConfig) params.config_id = selectedConfig;
      if (timeGranularity) params.granularity = timeGranularity;
      const response = await api.get('/forecasts/export', { params, responseType: 'blob' });
      const url = URL.createObjectURL(new Blob([response.data], { type: 'text/csv' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = `demand_plan_export_${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      // Fallback: export current page data as CSV
      const rows = [['Product', 'Site', 'Date', 'P10', 'Median', 'P90', 'Granularity']];
      rows.push(['Note: Connect to API for full export']);
      const blob = new Blob([rows.map((r) => r.join(',')).join('\n')], { type: 'text/csv' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `demand_plan_template_${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      URL.revokeObjectURL(url);
      setImportStatus({ type: 'info', message: 'Downloaded CSV template. Connect to API for full data export.' });
    }
  };

  // Import forecasts from CSV
  const handleImport = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setImportStatus(null);
    try {
      const formData = new FormData();
      formData.append('file', file);
      if (selectedConfig) formData.append('config_id', selectedConfig);
      const response = await api.post('/forecasts/import', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setImportStatus({ type: 'success', message: `Imported ${response.data.imported_count || 0} forecast records successfully.` });
    } catch (err) {
      setImportStatus({ type: 'error', message: `Import failed: ${err.response?.data?.detail || err.message}. Ensure CSV has columns: product_id, site_id, date, p10, p50, p90.` });
    }
    // Reset file input
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      {/* Header */}
      <div className="flex justify-between items-center mb-6">
        <div>
          <h1 className="text-2xl font-bold text-foreground">
            Demand Plan Editor
          </h1>
          <p className="text-sm text-muted-foreground">
            Adjust statistical forecasts with full audit trail and version control
          </p>
        </div>
        <div className="flex gap-2">
          <Badge variant="default" className="flex items-center gap-1">
            <Pencil className="h-3 w-3" />
            Editable Forecasts
          </Badge>
        </div>
      </div>

      {importStatus && (
        <Alert variant={importStatus.type === 'error' ? 'destructive' : importStatus.type === 'success' ? 'success' : 'info'} className="mb-4" onClose={() => setImportStatus(null)}>
          {importStatus.message}
        </Alert>
      )}

      {/* Info Alert */}
      <Alert variant="info" className="mb-6">
        <strong>How to use:</strong> Click any cell to edit the forecast value. Use bulk tools
        to apply percentage or delta adjustments to multiple cells. All changes are tracked with
        full audit history.
      </Alert>

      {/* Filters and Actions */}
      <Card className="mb-6">
        <CardContent className="pt-4">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
            <div>
              <Label>Supply Chain Config</Label>
              {configsLoading ? (
                <div className="flex items-center gap-2 py-2 mt-1">
                  <Spinner size="sm" />
                  <span className="text-sm text-muted-foreground">Loading...</span>
                </div>
              ) : configsError ? (
                <Alert variant="error" className="mt-1">{configsError}</Alert>
              ) : (
                <Select value={selectedConfig} onValueChange={setSelectedConfig}>
                  <SelectTrigger className="mt-1">
                    <SelectValue placeholder="All Configs" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="">All Configs</SelectItem>
                    {supplyChainConfigs.map(config => (
                      <SelectItem key={config.id} value={config.id.toString()}>
                        {config.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </div>
            <div>
              <Label>Time Granularity</Label>
              <Select value={timeGranularity} onValueChange={setTimeGranularity}>
                <SelectTrigger className="mt-1">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="day">Daily</SelectItem>
                  <SelectItem value="week">Weekly</SelectItem>
                  <SelectItem value="month">Monthly</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>{/* Spacer */}</div>
            <div className="flex gap-2">
              <input
                type="file"
                ref={fileInputRef}
                accept=".csv,.xlsx"
                onChange={handleImport}
                className="hidden"
              />
              <Button variant="outline" className="flex-1" leftIcon={<Upload className="h-4 w-4" />} onClick={() => fileInputRef.current?.click()}>
                Import
              </Button>
              <Button variant="outline" className="flex-1" leftIcon={<Download className="h-4 w-4" />} onClick={handleExport}>
                Export
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-4">
        <TabsList className="w-full grid grid-cols-4">
          <TabsTrigger value="edit" className="flex items-center gap-2">
            <Pencil className="h-4 w-4" />
            Edit Forecasts
          </TabsTrigger>
          <TabsTrigger value="pipeline" className="flex items-center gap-2">
            <Upload className="h-4 w-4" />
            ML Forecast Pipeline
          </TabsTrigger>
          <TabsTrigger value="history" className="flex items-center gap-2">
            <History className="h-4 w-4" />
            Adjustment History
          </TabsTrigger>
          <TabsTrigger value="compare" className="flex items-center gap-2">
            <ArrowLeftRight className="h-4 w-4" />
            Version Comparison
          </TabsTrigger>
        </TabsList>

        {/* Tab Content */}
        <TabsContent value="edit">
          <Card>
            <CardContent className="pt-4">
              <ForecastEditor
                configId={selectedConfig ? parseInt(selectedConfig) : undefined}
                onSave={() => console.log('Forecasts saved')}
              />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="pipeline">
          <ForecastPipelineManager configId={selectedConfig ? parseInt(selectedConfig) : undefined} />
        </TabsContent>

        <TabsContent value="history">
          <AdjustmentHistoryTab configId={selectedConfig ? parseInt(selectedConfig) : undefined} />
        </TabsContent>

        <TabsContent value="compare">
          <VersionComparisonTab configId={selectedConfig} />
        </TabsContent>
      </Tabs>
    </div>
  );
};

export default DemandPlanEdit;
