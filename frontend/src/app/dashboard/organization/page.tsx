'use client';

import { useState, useEffect } from 'react';
import * as api from '@/lib/api';
import { Button, Input, Alert } from '@/components/base';
import type { OrganizationMemberWithProfile, OrganizationInvite, CommodityGroup } from '@/types/database';

type Tab = 'members' | 'settings';

const ROLE_BADGES: Record<string, string> = {
  admin: 'bg-ink text-white',
  buyer: 'bg-accent-soft/40 text-accent-deep',
  requester: 'bg-ink-800/[0.06] text-ink/70',
};

export default function OrganizationPage() {
  const [tab, setTab] = useState<Tab>('members');
  const [members, setMembers] = useState<OrganizationMemberWithProfile[]>([]);
  const [invites, setInvites] = useState<OrganizationInvite[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState<api.MemberRole>('requester');
  const [inviting, setInviting] = useState(false);
  const [organizationName, setOrganizationName] = useState<string>('');
  const [role, setRole] = useState<api.MemberRole | null>(null);
  const [requiredFields, setRequiredFields] = useState<api.ConfigurableRequiredField[]>([]);
  const [autoApproveBelow, setAutoApproveBelow] = useState('');
  const [categoryRules, setCategoryRules] = useState<api.CategoryRule[]>([]);
  const [commodityGroups, setCommodityGroups] = useState<CommodityGroup[]>([]);
  const [ruleTerm, setRuleTerm] = useState('');
  const [ruleKind, setRuleKind] = useState('');
  const [ruleGroup, setRuleGroup] = useState('');
  const [ruleMaximum, setRuleMaximum] = useState('');
  const [savingSettings, setSavingSettings] = useState(false);

  useEffect(() => {
    loadOrganization();
  }, []);

  const loadOrganization = async () => {
    setLoading(true);
    setError('');

    try {
      const org = await api.organizations.getMine();
      setOrganizationName(org.name);
      setRole(org.role);

      const [result, settings, groups] = await Promise.all([
        api.organizations.getMembers(),
        api.organizations.getSettings(),
        api.commodityGroups.list(),
      ]);
      setMembers(result.members);
      setInvites(result.invites);
      setRequiredFields(settings.required_fields);
      setAutoApproveBelow(settings.auto_approve_below == null ? '' : String(settings.auto_approve_below));
      setCategoryRules(settings.category_rules);
      setCommodityGroups(groups);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load organization');
    }

    setLoading(false);
  };

  const handleInvite = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setSuccess('');
    setInviting(true);

    if (!inviteEmail || !inviteEmail.includes('@')) {
      setError('Please enter a valid email address');
      setInviting(false);
      return;
    }

    try {
      await api.organizations.invite(inviteEmail, inviteRole);
      setSuccess(`Invitation sent to ${inviteEmail}`);
      setInviteEmail('');
      await loadOrganization(); // Reload to show new invite
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to send invitation');
    }

    setInviting(false);
  };

  const handleCancelInvite = async (inviteId: string) => {
    try {
      await api.organizations.cancelInvite(inviteId);
      setSuccess('Invitation cancelled');
      await loadOrganization();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to cancel invitation');
    }
  };

  const REQUIRABLE_FIELDS: { key: api.ConfigurableRequiredField; label: string }[] = [
    { key: 'vat_id', label: 'Tax ID' },
    { key: 'department', label: 'Department' },
  ];

  const toggleRequiredField = async (field: api.ConfigurableRequiredField) => {
    const next = requiredFields.includes(field)
      ? requiredFields.filter((f) => f !== field)
      : [...requiredFields, field];
    setRequiredFields(next);
    setSavingSettings(true);
    setError('');
    setSuccess('');
    try {
      const saved = await api.organizations.updateSettings({ required_fields: next });
      setRequiredFields(saved.required_fields);
      setSuccess('Settings updated');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update settings');
      await loadOrganization(); // revert to server state
    }
    setSavingSettings(false);
  };

  const saveApprovalThreshold = async () => {
    const threshold = autoApproveBelow.trim() ? Number(autoApproveBelow) : null;
    if (threshold !== null && (!Number.isFinite(threshold) || threshold <= 0)) {
      setError('Enter a positive amount or leave the field empty to disable automatic approval.');
      return;
    }
    setSavingSettings(true);
    setError('');
    setSuccess('');
    try {
      const saved = await api.organizations.updateSettings({ auto_approve_below: threshold });
      setAutoApproveBelow(saved.auto_approve_below == null ? '' : String(saved.auto_approve_below));
      setSuccess('Approval threshold updated.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update approval threshold');
      await loadOrganization();
    }
    setSavingSettings(false);
  };

  const saveCategoryRules = async (rules: api.CategoryRule[]): Promise<boolean> => {
    setSavingSettings(true);
    setError('');
    setSuccess('');
    try {
      const saved = await api.organizations.updateSettings({ category_rules: rules });
      setCategoryRules(saved.category_rules);
      setSuccess('Booking rules updated.');
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update booking rules');
      return false;
    } finally {
      setSavingSettings(false);
    }
  };

  const addCategoryRule = async () => {
    const term = ruleTerm.trim();
    const maximum = ruleMaximum.trim() ? Number(ruleMaximum) : null;
    if ((!ruleKind && term.length < 2) || (ruleKind && term.length > 0) || !ruleGroup || (maximum !== null && (!Number.isFinite(maximum) || maximum < 0))) {
      setError('Enter a term, a commodity group and a valid optional maximum.');
      return;
    }
    if (categoryRules.some((rule) => ruleKind ? rule.item_kind === ruleKind : rule.term?.toLowerCase() === term.toLowerCase())) {
      setError('A rule with this term already exists.');
      return;
    }
    if (await saveCategoryRules([...categoryRules, { ...(ruleKind ? { item_kind: ruleKind as 'cable_tie' | 'printer_toner' | 'software_subscription' } : { term }), commodity_group_id: Number(ruleGroup), max_total: maximum }])) {
      setRuleTerm(''); setRuleKind(''); setRuleGroup(''); setRuleMaximum('');
    }
  };

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="flex items-center justify-center h-64">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-accent-deep"></div>
        </div>
      </div>
    );
  }

  // The whole page is for admins (the nav only shows it to them, but guard anyway).
  if (role !== null && role !== 'admin') {
    return (
      <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <Alert variant="info">
          Only organization admins can manage members and settings.
        </Alert>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-3xl font-semibold tracking-tight text-ink">{organizationName}</h1>
        <p className="text-ink/55 mt-1.5">
          Manage your organization&apos;s members and settings
        </p>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 mb-6">
        {(
          [
            { key: 'members', label: 'Members' },
            { key: 'settings', label: 'Settings' },
          ] as { key: Tab; label: string }[]
        ).map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 rounded-full text-sm font-medium transition-colors ${
              tab === t.key
                ? 'bg-ink text-white'
                : 'bg-white border border-line text-ink/70 hover:bg-ink-800/[0.03]'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {error && <Alert variant="error" className="mb-6">{error}</Alert>}
      {success && <Alert variant="success" className="mb-6">{success}</Alert>}

      {tab === 'members' && (
        <>
          {/* Invite Form */}
          <div className="lio-card p-6 mb-6">
            <h2 className="text-xl font-semibold tracking-tight text-ink mb-1">Invite New Member</h2>
            <p className="text-sm text-ink/50 mb-4">
              Invites are tracked in the app — no emails are sent.
            </p>
            <form onSubmit={handleInvite} className="flex flex-col sm:flex-row gap-3">
              <Input
                type="email"
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                placeholder="colleague@example.com"
                className="flex-1"
                disabled={inviting}
              />
              <select
                value={inviteRole}
                onChange={(e) => setInviteRole(e.target.value as api.MemberRole)}
                disabled={inviting}
                className="h-11 px-3.5 border border-line rounded-xl text-sm text-ink bg-white focus:outline-none focus:ring-2 focus:ring-accent-deep/60"
              >
                <option value="requester">Requester</option>
                <option value="buyer">Buyer</option>
                <option value="admin">Admin</option>
              </select>
              <Button type="submit" loading={inviting} disabled={inviting} className="min-w-[140px]">
                Send Invite
              </Button>
            </form>
          </div>

          {/* Current Members */}
          <div className="lio-card overflow-hidden mb-6">
            <div className="p-6 border-b border-line">
              <h2 className="text-xl font-semibold tracking-tight text-ink">
                Members ({members.length})
              </h2>
            </div>
            <div className="divide-y divide-line">
              {members.length === 0 ? (
                <div className="p-6 text-center text-ink/50">No members found</div>
              ) : (
                members.map((member) => (
                  <div key={member.id} className="p-6 flex items-center justify-between">
                    <div>
                      <p className="text-sm font-medium text-ink">
                        {member.profiles?.full_name || 'Unknown User'}
                      </p>
                      <p className="text-sm text-ink/50">{member.profiles?.email}</p>
                    </div>
                    <span
                      className={`inline-flex px-3 py-1 text-xs font-semibold rounded-full ${
                        ROLE_BADGES[member.role] || ROLE_BADGES.requester
                      }`}
                    >
                      {member.role}
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Pending Invites */}
          {invites.length > 0 && (
            <div className="lio-card overflow-hidden">
              <div className="p-6 border-b border-line">
                <h2 className="text-xl font-semibold tracking-tight text-ink">
                  Pending Invites ({invites.length})
                </h2>
              </div>
              <div className="divide-y divide-line">
                {invites.map((invite) => (
                  <div key={invite.id} className="p-6 flex items-center justify-between gap-4">
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-ink">{invite.email}</p>
                      <p className="text-sm text-ink/50">
                        Invited {new Date(invite.created_at).toLocaleDateString('en-US')} · as {invite.role}
                      </p>
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleCancelInvite(invite.id)}
                    >
                      Cancel
                    </Button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}

      {tab === 'settings' && (
        <div className="lio-card p-6">
          <h2 className="text-xl font-semibold tracking-tight text-ink">Request Settings</h2>
          <p className="text-sm text-ink/55 mt-1 mb-4">
            Choose which optional fields are required when creating a request.
          </p>
          <div className="space-y-2">
            {REQUIRABLE_FIELDS.map((field) => {
              const checked = requiredFields.includes(field.key);
              return (
                <label
                  key={field.key}
                  className="flex items-center gap-3 rounded-xl border border-line px-4 py-3 cursor-pointer hover:bg-ink-800/[0.02]"
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={savingSettings}
                    onChange={() => toggleRequiredField(field.key)}
                    className="h-4 w-4 accent-accent-deep"
                  />
                  <span className="text-sm text-ink">Require {field.label}</span>
                </label>
              );
            })}
          </div>
          <div className="mt-7 border-t border-line pt-6">
            <h3 className="text-base font-semibold text-ink">Automatic approval</h3>
            <p className="text-sm text-ink/55 mt-1 mb-3">
              Requests strictly below this total are approved automatically. Use the full request total including shipping and tax. Leave empty to require buyer approval for every request.
            </p>
            <div className="flex flex-col sm:flex-row gap-3 sm:items-end">
              <Input label="Threshold ($)" type="number" min="0.01" step="0.01" value={autoApproveBelow}
                onChange={(event) => setAutoApproveBelow(event.target.value)} placeholder="Disabled" />
              <Button type="button" onClick={saveApprovalThreshold} disabled={savingSettings}>Save threshold</Button>
            </div>
          </div>
          <div className="mt-7 border-t border-line pt-6">
            <h3 className="text-base font-semibold text-ink">Booking category rules</h3>
            <p className="text-sm text-ink/55 mt-1 mb-3">Article kinds and custom terms apply only to this organization. A maximum limits the rule by the full request total. Acme treats software subscriptions up to $500 as small; confirm this policy with the customer.</p>
            <div className="space-y-2 mb-4">
              {categoryRules.map((rule) => (
                <div key={rule.item_kind || rule.term} className="flex items-center justify-between gap-3 rounded-xl border border-line px-4 py-3 text-sm">
                  <span>{rule.item_kind?.replaceAll('_', ' ') || rule.term} → {commodityGroups.find((group) => group.id === rule.commodity_group_id)?.name || rule.commodity_group_id}
                    {rule.max_total != null ? ` · up to $${rule.max_total}` : ''}</span>
                  <button type="button" className="text-red-600 disabled:opacity-50" disabled={savingSettings}
                    onClick={() => saveCategoryRules(categoryRules.filter((item) => (item.item_kind || item.term) !== (rule.item_kind || rule.term)))}>Remove</button>
                </div>
              ))}
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div>
                <label className="text-sm text-ink/70">Article kind
                  <select className="mt-1.5 h-11 w-full rounded-xl border border-line px-3 text-ink" value={ruleKind} onChange={(event) => { setRuleKind(event.target.value); setRuleTerm(''); }}>
                    <option value="">Custom text term</option><option value="cable_tie">Cable ties</option>
                    <option value="printer_toner">Printer toner</option><option value="software_subscription">Software subscription</option>
                  </select>
                </label>
                {!ruleKind && <Input label="Item term" value={ruleTerm} onChange={(event) => setRuleTerm(event.target.value)} placeholder="e.g. cable ties" />}
              </div>
              <label className="text-sm text-ink/70">Commodity group
                <select className="mt-1.5 h-11 w-full rounded-xl border border-line px-3 text-ink" value={ruleGroup} onChange={(event) => setRuleGroup(event.target.value)}>
                  <option value="">Select a group</option>
                  {commodityGroups.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}
                </select>
              </label>
              <Input label="Maximum total ($), optional" type="number" min="0" step="0.01" value={ruleMaximum} onChange={(event) => setRuleMaximum(event.target.value)} />
            </div>
            <Button type="button" className="mt-3" onClick={addCategoryRule} disabled={savingSettings}>Add rule</Button>
          </div>
        </div>
      )}
    </div>
  );
}
