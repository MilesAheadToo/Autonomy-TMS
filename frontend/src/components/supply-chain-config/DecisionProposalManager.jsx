/**
 * Decision Proposal Manager
 *
 * Manages decision proposals for approval workflows.
 * Enables agents and humans to propose actions, simulate business impact,
 * and present business cases for approval.
 *
 * Features:
 * - List proposals with status filtering
 * - Create new proposals linked to scenario branches
 * - Compute business impact (probabilistic balanced scorecard)
 * - View business case with financial/customer/operational/strategic metrics
 * - Approve/reject workflow with authority checks
 */

import React, { useState, useEffect } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  CardContent,
  Input,
  Label,
  Modal,
  Spinner,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  Textarea,
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '../common';
import {
  Plus,
  Check,
  X,
  BarChart3,
  Eye,
  TrendingUp,
  TrendingDown,
  Calculator,
} from 'lucide-react';
import { useSnackbar } from 'notistack';
import { api } from '../../services/api';
import { useAuth } from '../../contexts/AuthContext';

const DecisionProposalManager = ({ configId, scenarioName, onProposalChange }) => {
  const { user } = useAuth();
  const [proposals, setProposals] = useState([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState('all');
  const [selectedProposal, setSelectedProposal] = useState(null);
  const [detailsDialogOpen, setDetailsDialogOpen] = useState(false);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [computingImpact, setComputingImpact] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);

  // Create proposal form state
  const [newProposal, setNewProposal] = useState({
    title: '',
    description: '',
    action_type: 'increase_safety_stock',
    action_params: {},
  });

  const { enqueueSnackbar } = useSnackbar();

  useEffect(() => {
    if (configId) {
      loadProposals();
    }
  }, [configId, statusFilter]);

  const loadProposals = async () => {
    try {
      setLoading(true);
      const params = statusFilter !== 'all' ? { status: statusFilter } : {};
      const response = await api.get(`/supply-chain-config/${configId}/proposals`, { params });
      setProposals(response.data.proposals);
    } catch (error) {
      console.error('Failed to load proposals:', error);
      enqueueSnackbar('Failed to load proposals', { variant: 'error' });
    } finally {
      setLoading(false);
    }
  };

  const handleCreateProposal = async () => {
    if (!newProposal.title.trim()) {
      enqueueSnackbar('Title is required', { variant: 'error' });
      return;
    }

    try {
      setActionLoading(true);
      const response = await api.post(`/supply-chain-config/${configId}/proposals`, {
        ...newProposal,
        proposed_by: user?.email || 'unknown',
        proposed_by_type: 'human',
      });

      enqueueSnackbar(response.data.message || 'Proposal created successfully', {
        variant: 'success',
      });

      // Close dialog and reload
      setCreateDialogOpen(false);
      setNewProposal({
        title: '',
        description: '',
        action_type: 'increase_safety_stock',
        action_params: {},
      });
      loadProposals();

      if (onProposalChange) {
        onProposalChange(response.data);
      }
    } catch (error) {
      console.error('Failed to create proposal:', error);
      enqueueSnackbar(error.response?.data?.detail || 'Failed to create proposal', {
        variant: 'error',
      });
    } finally {
      setActionLoading(false);
    }
  };

  const handleViewDetails = async (proposalId) => {
    try {
      const response = await api.get(`/supply-chain-config/proposals/${proposalId}`);
      setSelectedProposal(response.data);
      setDetailsDialogOpen(true);
    } catch (error) {
      console.error('Failed to load proposal details:', error);
      enqueueSnackbar('Failed to load proposal details', { variant: 'error' });
    }
  };

  const handleComputeImpact = async (proposalId) => {
    try {
      setComputingImpact(true);
      const response = await api.post(
        `/supply-chain-config/proposals/${proposalId}/compute-impact`,
        {
          planning_horizon: 52,
          simulation_runs: 1000,
        }
      );

      enqueueSnackbar('Business impact computed successfully', { variant: 'success' });

      // Reload proposal details
      handleViewDetails(proposalId);
      loadProposals();
    } catch (error) {
      console.error('Failed to compute impact:', error);
      enqueueSnackbar(error.response?.data?.detail || 'Failed to compute impact', {
        variant: 'error',
      });
    } finally {
      setComputingImpact(false);
    }
  };

  const handleApprove = async (proposalId) => {
    try {
      setActionLoading(true);
      const response = await api.post(
        `/supply-chain-config/proposals/${proposalId}/approve`,
        {
          approved_by: user?.email || 'unknown',
          commit_to_parent: true,
        }
      );

      enqueueSnackbar('Proposal approved successfully', { variant: 'success' });
      setDetailsDialogOpen(false);
      loadProposals();

      if (onProposalChange) {
        onProposalChange(response.data);
      }
    } catch (error) {
      console.error('Failed to approve proposal:', error);
      enqueueSnackbar(error.response?.data?.detail || 'Failed to approve proposal', {
        variant: 'error',
      });
    } finally {
      setActionLoading(false);
    }
  };

  const handleReject = async (proposalId, reason) => {
    try {
      setActionLoading(true);
      const response = await api.post(
        `/supply-chain-config/proposals/${proposalId}/reject`,
        {
          rejected_by: user?.email || 'unknown',
          reason: reason || 'No reason provided',
          delete_scenario: true,
        }
      );

      enqueueSnackbar('Proposal rejected successfully', { variant: 'success' });
      setDetailsDialogOpen(false);
      loadProposals();

      if (onProposalChange) {
        onProposalChange(response.data);
      }
    } catch (error) {
      console.error('Failed to reject proposal:', error);
      enqueueSnackbar(error.response?.data?.detail || 'Failed to reject proposal', {
        variant: 'error',
      });
    } finally {
      setActionLoading(false);
    }
  };

  const getStatusVariant = (status) => {
    switch (status) {
      case 'pending':
        return 'warning';
      case 'approved':
        return 'success';
      case 'rejected':
        return 'destructive';
      case 'executed':
        return 'info';
      default:
        return 'secondary';
    }
  };

  const formatPercent = (value) => {
    return `${(value * 100).toFixed(1)}%`;
  };

  const formatCurrency = (value) => {
    return `$${value.toLocaleString()}`;
  };

  if (loading) {
    return (
      <div className="flex justify-center items-center min-h-[200px]">
        <Spinner size="lg" />
      </div>
    );
  }

  return (
    <div>
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <BarChart3 className="h-5 w-5 text-primary" />
        <h3 className="text-lg font-semibold">Decision Proposals</h3>
        <div className="flex-1" />
        <Button
          onClick={() => setCreateDialogOpen(true)}
          disabled={actionLoading}
          leftIcon={<Plus className="h-4 w-4" />}
        >
          Create Proposal
        </Button>
      </div>

      {/* Status Filter */}
      <Tabs value={statusFilter} onValueChange={setStatusFilter} className="mb-4">
        <TabsList>
          <TabsTrigger value="all">All</TabsTrigger>
          <TabsTrigger value="pending">Pending</TabsTrigger>
          <TabsTrigger value="approved">Approved</TabsTrigger>
          <TabsTrigger value="rejected">Rejected</TabsTrigger>
          <TabsTrigger value="executed">Executed</TabsTrigger>
        </TabsList>
      </Tabs>

      {/* Proposals List */}
      {proposals.length === 0 ? (
        <Alert variant="info">
          No proposals found. Click "Create Proposal" to start a decision simulation.
        </Alert>
      ) : (
        <div className="border rounded-md">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Title</TableHead>
                <TableHead>Action Type</TableHead>
                <TableHead>Proposed By</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Created</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {proposals.map((proposal) => (
                <TableRow key={proposal.id}>
                  <TableCell>{proposal.title}</TableCell>
                  <TableCell>{proposal.action_type}</TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      {proposal.proposed_by}
                      <Badge variant="secondary" className="text-xs">
                        {proposal.proposed_by_type}
                      </Badge>
                    </div>
                  </TableCell>
                  <TableCell>
                    <Badge variant={getStatusVariant(proposal.status)}>{proposal.status}</Badge>
                  </TableCell>
                  <TableCell>{new Date(proposal.created_at).toLocaleDateString()}</TableCell>
                  <TableCell className="text-right">
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handleViewDetails(proposal.id)}
                          >
                            <Eye className="h-4 w-4" />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>View Details</TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                    {proposal.status === 'pending' && (
                      <>
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => handleComputeImpact(proposal.id)}
                                disabled={computingImpact}
                              >
                                <Calculator className="h-4 w-4" />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>Compute Impact</TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => handleApprove(proposal.id)}
                                disabled={actionLoading}
                                className="text-green-600"
                              >
                                <Check className="h-4 w-4" />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>Approve</TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => handleReject(proposal.id, 'Rejected by user')}
                                disabled={actionLoading}
                                className="text-destructive"
                              >
                                <X className="h-4 w-4" />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>Reject</TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                      </>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {/* Create Proposal Dialog */}
      <Modal
        isOpen={createDialogOpen}
        onClose={() => !actionLoading && setCreateDialogOpen(false)}
        title="Create Decision Proposal"
        size="lg"
        footer={
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setCreateDialogOpen(false)} disabled={actionLoading}>
              Cancel
            </Button>
            <Button
              onClick={handleCreateProposal}
              disabled={actionLoading || !newProposal.title.trim()}
              leftIcon={actionLoading ? <Spinner size="sm" /> : <Plus className="h-4 w-4" />}
            >
              Create Proposal
            </Button>
          </div>
        }
      >
        <div className="space-y-4">
          <div>
            <Label htmlFor="proposal-title">Title</Label>
            <Input
              id="proposal-title"
              value={newProposal.title}
              onChange={(e) => setNewProposal({ ...newProposal, title: e.target.value })}
              placeholder="e.g., Expedite shipment from Asia"
              disabled={actionLoading}
            />
          </div>
          <div>
            <Label htmlFor="proposal-description">Description</Label>
            <Textarea
              id="proposal-description"
              value={newProposal.description}
              onChange={(e) => setNewProposal({ ...newProposal, description: e.target.value })}
              rows={3}
              placeholder="Describe the proposed change and business rationale..."
              disabled={actionLoading}
            />
          </div>
          <div>
            <Label htmlFor="proposal-action-type">Action Type</Label>
            <select
              id="proposal-action-type"
              value={newProposal.action_type}
              onChange={(e) => setNewProposal({ ...newProposal, action_type: e.target.value })}
              disabled={actionLoading}
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              <option value="expedite">Expedite Shipment</option>
              <option value="increase_safety_stock">Increase Safety Stock</option>
              <option value="add_supplier">Add Supplier</option>
              <option value="change_sourcing_rule">Change Sourcing Rule</option>
              <option value="expand_capacity">Expand Capacity</option>
              <option value="network_redesign">Network Redesign</option>
            </select>
          </div>
          <Alert variant="info">
            <p className="text-sm">
              <strong>Decision Simulation:</strong> This proposal will create a child scenario to
              simulate the business impact of your proposed change. The system will compute
              probabilistic metrics (cost, service level, inventory) and present a business case for
              approval.
            </p>
          </Alert>
        </div>
      </Modal>

      {/* Proposal Details Dialog */}
      {selectedProposal && (
        <Modal
          isOpen={detailsDialogOpen}
          onClose={() => !actionLoading && setDetailsDialogOpen(false)}
          title={
            <div className="flex items-center gap-3">
              {selectedProposal.title}
              <Badge variant={getStatusVariant(selectedProposal.status)}>
                {selectedProposal.status}
              </Badge>
            </div>
          }
          size="xl"
          footer={
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setDetailsDialogOpen(false)} disabled={actionLoading}>
                Close
              </Button>
              {selectedProposal.status === 'pending' && (
                <>
                  {!selectedProposal.business_case && (
                    <Button
                      variant="outline"
                      onClick={() => handleComputeImpact(selectedProposal.id)}
                      disabled={computingImpact}
                      leftIcon={computingImpact ? <Spinner size="sm" /> : <Calculator className="h-4 w-4" />}
                    >
                      Compute Impact
                    </Button>
                  )}
                  <Button
                    variant="outline"
                    onClick={() => handleReject(selectedProposal.id, 'Rejected by user')}
                    disabled={actionLoading}
                    className="text-destructive"
                    leftIcon={<X className="h-4 w-4" />}
                  >
                    Reject
                  </Button>
                  <Button
                    onClick={() => handleApprove(selectedProposal.id)}
                    disabled={actionLoading}
                    className="bg-green-600 hover:bg-green-700"
                    leftIcon={<Check className="h-4 w-4" />}
                  >
                    Approve
                  </Button>
                </>
              )}
            </div>
          }
        >
          <div className="space-y-6">
            {/* Metadata */}
            <div>
              <p className="text-sm text-muted-foreground">Description</p>
              <p className="text-sm">{selectedProposal.description}</p>
            </div>

            <div>
              <p className="text-sm text-muted-foreground">Action Type</p>
              <p className="text-sm">{selectedProposal.action_type}</p>
            </div>

            <hr />

            {/* Business Case */}
            {selectedProposal.business_case ? (
              <div>
                <h4 className="text-lg font-semibold mb-4">Business Case</h4>
                <Alert variant="info" className="mb-4">
                  {selectedProposal.business_case.summary}
                </Alert>

                {/* Recommendation */}
                <Card className="mb-4 bg-primary/10">
                  <CardContent className="pt-4">
                    <p className="font-medium mb-2">Recommendation</p>
                    <p className="text-sm">{selectedProposal.business_case.recommendation}</p>
                  </CardContent>
                </Card>

                {/* Financial Impact */}
                {selectedProposal.financial_impact && (
                  <Card className="mb-4">
                    <CardContent className="pt-4">
                      <p className="font-medium mb-3">Financial Impact</p>
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>Metric</TableHead>
                            <TableHead className="text-right">P10</TableHead>
                            <TableHead className="text-right">Median</TableHead>
                            <TableHead className="text-right">P90</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {Object.entries(selectedProposal.financial_impact).map(([metric, values]) => (
                            <TableRow key={metric}>
                              <TableCell>{metric.replace(/_/g, ' ')}</TableCell>
                              <TableCell className="text-right">
                                <div className="flex items-center justify-end gap-1">
                                  {formatCurrency(values.p10)}
                                  {values.p10 > 0 ? (
                                    <TrendingUp className="h-4 w-4 text-destructive" />
                                  ) : (
                                    <TrendingDown className="h-4 w-4 text-green-600" />
                                  )}
                                </div>
                              </TableCell>
                              <TableCell className="text-right">{formatCurrency(values.p50)}</TableCell>
                              <TableCell className="text-right">{formatCurrency(values.p90)}</TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </CardContent>
                  </Card>
                )}

                {/* Operational Impact */}
                {selectedProposal.operational_impact && (
                  <Card className="mb-4">
                    <CardContent className="pt-4">
                      <p className="font-medium mb-3">Operational Impact</p>
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>Metric</TableHead>
                            <TableHead className="text-right">P10</TableHead>
                            <TableHead className="text-right">Median</TableHead>
                            <TableHead className="text-right">P90</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {Object.entries(selectedProposal.operational_impact).map(
                            ([metric, values]) => (
                              <TableRow key={metric}>
                                <TableCell>{metric.replace(/_/g, ' ')}</TableCell>
                                <TableCell className="text-right">
                                  {metric.includes('rate')
                                    ? formatPercent(values.p10)
                                    : values.p10.toFixed(2)}
                                </TableCell>
                                <TableCell className="text-right">
                                  {metric.includes('rate')
                                    ? formatPercent(values.p50)
                                    : values.p50.toFixed(2)}
                                </TableCell>
                                <TableCell className="text-right">
                                  {metric.includes('rate')
                                    ? formatPercent(values.p90)
                                    : values.p90.toFixed(2)}
                                </TableCell>
                              </TableRow>
                            )
                          )}
                        </TableBody>
                      </Table>
                    </CardContent>
                  </Card>
                )}

                {/* Risks */}
                {selectedProposal.business_case.risks && (
                  <Alert variant="warning">
                    <p className="font-medium mb-2">Risks</p>
                    <ul className="list-disc list-inside">
                      {selectedProposal.business_case.risks.map((risk, idx) => (
                        <li key={idx} className="text-sm">
                          {risk}
                        </li>
                      ))}
                    </ul>
                  </Alert>
                )}
              </div>
            ) : (
              <Alert variant="warning">
                Business impact not yet computed. Click "Compute Impact" to run simulation.
              </Alert>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
};

export default DecisionProposalManager;
