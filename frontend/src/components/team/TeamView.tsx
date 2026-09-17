import { Building2, Check, ChevronDown, Copy, Crown, LoaderCircle, MoreHorizontal, RotateCcw, ShieldCheck, UserMinus, UserPlus, Users } from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { toast } from "sonner";
import { Avatar } from "@/components/audit/status";
import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorBoundary, PanelError } from "@/components/ui/error-boundary";
import { Segmented } from "@/components/ui/segmented";
import { Skeleton } from "@/components/ui/skeleton";
import { useNow } from "@/hooks/use-now";
import { api, toApiError, type ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { relativeTime } from "@/lib/dates";
import type { Organization, Role, User } from "@/lib/types";
import { useAudit, useOrganization } from "@/state/audit-store";

const ROLES: { value: Role; label: string; summary: string }[] = [
  { value: "owner", label: "Owner", summary: "Everything, including adding and removing other owners." },
  { value: "admin", label: "Admin", summary: "Invites people, changes roles, and approves invoices." },
  { value: "reviewer", label: "Reviewer", summary: "Reviews flags, approves & archives invoices, uploads." },
  { value: "member", label: "Member", summary: "Views invoices and uploads them. Can't approve." },
];

const ROLE_LABEL: Record<Role, string> = { owner: "Owner", admin: "Admin", reviewer: "Reviewer", member: "Member" };

/** What a signed-in manager may change, mirroring the API's rules so the UI never offers a 403. */
function permissions(me: User | null) {
  const canManage = me !== null && (me.role === "owner" || me.role === "admin");
  return {
    canManage,
    assignable: (me?.role === "owner" ? ROLES : ROLES.filter((role) => role.value !== "owner")).map((role) => role.value),
    canEdit: (target: User) => canManage && target.id !== me!.id && (me!.role === "owner" || target.role !== "owner"),
  };
}

function inviteMessage(email: string, organization: string): string {
  return `You've been given access to ${organization} on Billvery. Sign in at ${window.location.origin} with your Google account ${email}.`;
}

/** Owners and admins invite Google accounts here; everyone can see who is on the team. */
export function TeamView() {
  const { session, reloadUsers } = useAudit();
  const organization = useOrganization();
  const me = session?.user ?? null;
  const rules = permissions(me);
  const now = useNow(60_000);
  const [members, setMembers] = useState<User[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [filter, setFilter] = useState<"active" | "removed">("active");

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    api.teamMembers(controller.signal).then(setMembers, (failure: unknown) => {
      const apiError = toApiError(failure);
      if (!apiError.aborted) setError(apiError);
    });
    return () => controller.abort();
  }, [attempt]);

  const upsert = (user: User) => {
    setMembers((list) => (list?.some((m) => m.id === user.id) ? list.map((m) => (m.id === user.id ? user : m)) : [user, ...(list ?? [])]));
    void reloadUsers();
  };

  const active = useMemo(() => (members ?? []).filter((m) => m.active), [members]);
  const removed = useMemo(() => (members ?? []).filter((m) => !m.active), [members]);
  const shown = filter === "active" ? active : removed;
  const invitedCount = active.filter((m) => m.last_login_at === null).length;

  return (
    <main className="mx-auto flex max-w-[1600px] animate-fade-in flex-col gap-5 px-4 pb-16 pt-6 sm:px-6">
      <PageHeader
        title="Team"
        description={
          <>
            People who share the <span className="text-ink-2">{organization.name}</span> workspace and its invoices
            {members ? ` · ${active.length} with access${invitedCount ? `, ${invitedCount} not signed in yet` : ""}` : ""}
          </>
        }
      />

      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
        <ErrorBoundary label="The team list">
          <section aria-labelledby="members-heading" className="glass overflow-hidden rounded-xl">
            <div className="flex flex-wrap items-center gap-3 px-4 pb-3 pt-4">
              <h2 id="members-heading" className="text-[15px] font-semibold tracking-[-0.01em]">
                Members
              </h2>
              <Segmented
                className="ml-auto"
                label="Show"
                value={filter}
                onValueChange={setFilter}
                options={[
                  { value: "active", label: "Has access", count: members ? active.length : undefined },
                  { value: "removed", label: "Access removed", count: members ? removed.length : undefined },
                ]}
              />
            </div>

            {error ? (
              <div className="border-t border-line p-4">
                <PanelError label="The team list" error={error} onRetry={() => setAttempt((n) => n + 1)} />
              </div>
            ) : members === null ? (
              <ul className="divide-y divide-line border-t border-line" aria-hidden>
                {[0, 1, 2].map((row) => (
                  <li key={row} className="flex h-[64px] items-center gap-3 px-4">
                    <Skeleton className="size-8 rounded-full" />
                    <div className="flex-1">
                      <Skeleton className="h-3.5 w-40" />
                      <Skeleton className="mt-2 h-3 w-56" />
                    </div>
                    <Skeleton className="h-7 w-24 rounded-md" />
                  </li>
                ))}
              </ul>
            ) : shown.length === 0 ? (
              <div className="border-t border-line">
                {filter === "removed" ? (
                  <EmptyState icon={ShieldCheck} tone="approved" title="Nobody's access has been removed" description="People you remove appear here, and their access can be restored at any time." />
                ) : (
                  <EmptyState icon={Users} title="No one has access yet" description="Invite the Google accounts that should be able to sign in." />
                )}
              </div>
            ) : (
              <ul className="divide-y divide-line border-t border-line">
                {shown.map((member) => (
                  <MemberRow
                    key={member.id}
                    member={member}
                    isMe={member.id === me?.id}
                    editable={rules.canEdit(member)}
                    assignable={rules.assignable}
                    organization={organization.name}
                    now={now}
                    onChanged={upsert}
                  />
                ))}
              </ul>
            )}
          </section>
        </ErrorBoundary>

        <aside className="flex flex-col gap-5">
          {rules.canManage ? (
            <InviteCard assignable={rules.assignable} organization={organization.name} onInvited={upsert} />
          ) : (
            <section className="glass rounded-xl px-4 py-4">
              <h2 className="flex items-center gap-2 text-[15px] font-semibold tracking-[-0.01em]">
                <UserPlus className="size-4 text-ink-3" aria-hidden /> Invite people
              </h2>
              <p className="mt-1.5 text-[13px] leading-relaxed text-ink-2">
                {me
                  ? "Only owners and admins can invite people or change roles. Ask one of them to add who you need."
                  : "Sign in with Google as an owner or admin to invite people. This console is using the development API key."}
              </p>
            </section>
          )}
          {rules.canManage ? <WorkspaceSettings organization={organization} /> : null}
          <RolesGuide />
        </aside>
      </div>
    </main>
  );
}

function InviteCard({ assignable, organization, onInvited }: { assignable: Role[]; organization: string; onInvited: (user: User) => void }) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("reviewer");
  const [sending, setSending] = useState(false);
  const [invited, setInvited] = useState<User | null>(null);
  const [copied, setCopied] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const address = email.trim();
    if (!address || sending) return;
    setSending(true);
    try {
      const user = await api.inviteMember({ email: address, role });
      onInvited(user);
      setInvited(user);
      setCopied(false);
      setEmail("");
      toast.success(`${user.email} can now sign in`, { description: `Added as ${ROLE_LABEL[user.role].toLowerCase()}. Share the sign-in message with them.` });
    } catch (failure) {
      toast.error("Couldn't invite them", { description: toApiError(failure).message });
    } finally {
      setSending(false);
    }
  };

  const copy = (text: string) =>
    navigator.clipboard?.writeText(text).then(
      () => setCopied(true),
      () => toast.error("Couldn't copy", { description: "Select the message and copy it instead." }),
    );

  return (
    <section aria-labelledby="invite-heading" className="glass rounded-xl px-4 pb-4 pt-4">
      <h2 id="invite-heading" className="flex items-center gap-2 text-[15px] font-semibold tracking-[-0.01em]">
        <UserPlus className="size-4 text-ink-3" aria-hidden /> Invite people
      </h2>
      <p className="mt-1 text-[12.5px] leading-relaxed text-ink-3">
        They'll see this workspace's invoices when they sign in with this Google account. No email is sent, so share the message below.
      </p>

      <form onSubmit={(event) => void submit(event)} className="mt-3.5 flex flex-col gap-3">
        <label className="flex flex-col gap-1.5">
          <span className="eyebrow">Google account</span>
          <input
            type="email"
            required
            autoComplete="off"
            spellCheck={false}
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="name@gmail.com"
            className="h-9 w-full rounded-lg border border-line bg-surface-solid px-3 text-[13px] text-ink shadow-[var(--shadow-card)] placeholder:text-ink-3 transition-[border-color,box-shadow] focus:border-accent/60 focus:shadow-[0_0_0_3px_color-mix(in_oklab,var(--accent)_18%,transparent)] focus:outline-none"
          />
        </label>
        <div className="flex flex-col gap-1.5">
          <span className="eyebrow">Role</span>
          <Segmented
            label="Role"
            value={role}
            onValueChange={setRole}
            className="w-full [&>*]:flex-1 [&>*]:justify-center"
            options={ROLES.filter((option) => assignable.includes(option.value)).map((option) => ({ value: option.value, label: option.label }))}
          />
          <p className="text-[11.5px] text-ink-3">{ROLES.find((option) => option.value === role)?.summary}</p>
        </div>
        <Button type="submit" variant="primary" size="lg" disabled={sending || !email.trim()}>
          {sending ? <LoaderCircle className="animate-spin" aria-hidden /> : <UserPlus aria-hidden />}
          {sending ? "Adding…" : "Give access"}
        </Button>
      </form>

      {invited ? (
        <div className="mt-4 animate-rise-in rounded-lg border border-approved/25 bg-approved/[0.06] px-3 py-3">
          <p className="flex items-center gap-1.5 text-[12.5px] font-medium text-ink">
            <Check className="size-3.5 text-approved" aria-hidden /> {invited.email} can sign in now
          </p>
          <p className="mt-1.5 select-all rounded-md border border-line bg-surface-solid px-2.5 py-2 text-[12px] leading-relaxed text-ink-2">
            {inviteMessage(invited.email, organization)}
          </p>
          <Button size="sm" variant="secondary" className="mt-2" onClick={() => void copy(inviteMessage(invited.email, organization))}>
            {copied ? <Check className="text-approved" aria-hidden /> : <Copy aria-hidden />}
            {copied ? "Copied" : "Copy message"}
          </Button>
        </div>
      ) : null}
    </section>
  );
}

function MemberRow({
  member,
  isMe,
  editable,
  assignable,
  organization,
  now,
  onChanged,
}: {
  member: User;
  isMe: boolean;
  editable: boolean;
  assignable: Role[];
  organization: string;
  now: number;
  onChanged: (user: User) => void;
}) {
  const [busy, setBusy] = useState(false);

  const change = async (body: { role?: Role; active?: boolean }, success: string, undo?: { role?: Role; active?: boolean }) => {
    setBusy(true);
    try {
      const updated = await api.updateMember(member.id, body);
      onChanged(updated);
      toast.success(success, undo ? { action: { label: "Undo", onClick: () => void change(undo, `Undone for ${member.email}`) } } : undefined);
    } catch (failure) {
      toast.error(`Couldn't update ${member.email}`, { description: toApiError(failure).message });
    } finally {
      setBusy(false);
    }
  };

  const status = !member.active
    ? { label: "Access removed", tone: "bg-ink-3/10 text-ink-3 ring-line" }
    : member.last_login_at
      ? { label: `Signed in ${relativeTime(member.last_login_at, now)}`, tone: "bg-approved/10 text-approved ring-approved/25" }
      : { label: "Invited · not signed in yet", tone: "bg-review/10 text-review ring-review/30" };

  return (
    <li className={cn("flex min-h-[64px] flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2.5", !member.active && "opacity-70")}>
      <Avatar name={member.name} className="size-8 text-[11px]" />
      <div className="min-w-0 flex-1">
        <p className="flex items-center gap-2 truncate text-[13px] font-medium text-ink">
          <span className="truncate">{member.name}</span>
          {isMe ? <span className="shrink-0 rounded-full bg-accent/10 px-1.5 text-[10.5px] font-medium leading-4 text-accent ring-1 ring-inset ring-accent/20">You</span> : null}
        </p>
        <p className="truncate text-[12px] text-ink-3">{member.email}</p>
      </div>

      <span className={cn("hidden h-[22px] shrink-0 items-center rounded-full px-2 text-[11.5px] font-medium ring-1 ring-inset sm:inline-flex", status.tone)}>{status.label}</span>

      {editable && member.active ? (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button size="sm" variant="secondary" disabled={busy} aria-label={`Role for ${member.email}: ${ROLE_LABEL[member.role]}`} className="w-[118px] justify-between">
              <span className="flex items-center gap-1.5">
                {member.role === "owner" ? <Crown className="text-review" aria-hidden /> : null}
                {ROLE_LABEL[member.role]}
              </span>
              {busy ? <LoaderCircle className="animate-spin text-ink-3" aria-hidden /> : <ChevronDown className="text-ink-3" aria-hidden />}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-72">
            <DropdownMenuLabel>Role</DropdownMenuLabel>
            <DropdownMenuRadioGroup
              value={member.role}
              onValueChange={(next) => {
                if (next !== member.role) void change({ role: next as Role }, `${member.email} is now ${ROLE_LABEL[next as Role].toLowerCase()}`, { role: member.role });
              }}
            >
              {ROLES.filter((role) => assignable.includes(role.value)).map((role) => (
                <DropdownMenuRadioItem key={role.value} value={role.value} className="items-start py-2">
                  <span>
                    <span className="block text-[13px]">{role.label}</span>
                    <span className="block text-[11px] leading-snug text-ink-3">{role.summary}</span>
                  </span>
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      ) : (
        <span className="inline-flex w-[118px] shrink-0 items-center justify-center gap-1.5 rounded-md border border-line px-2.5 py-1 text-[12px] font-medium text-ink-2">
          {member.role === "owner" ? <Crown className="size-3.5 text-review" aria-hidden /> : null}
          {ROLE_LABEL[member.role]}
        </span>
      )}

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button size="icon-sm" variant="ghost" aria-label={`More for ${member.email}`} className="data-[state=open]:bg-hover">
            <MoreHorizontal />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-60">
          <DropdownMenuItem
            onSelect={() =>
              void navigator.clipboard?.writeText(inviteMessage(member.email, organization)).then(
                () => toast.success("Sign-in message copied", { description: member.email }),
                () => undefined,
              )
            }
          >
            <Copy className="text-ink-3" aria-hidden /> Copy sign-in message
          </DropdownMenuItem>
          {editable ? (
            <>
              <DropdownMenuSeparator />
              {member.active ? (
                <DropdownMenuItem className="text-rejected data-[highlighted]:text-rejected" onSelect={() => void change({ active: false }, `Access removed for ${member.email}`, { active: true })}>
                  <UserMinus aria-hidden /> Remove access
                </DropdownMenuItem>
              ) : (
                <DropdownMenuItem onSelect={() => void change({ active: true }, `Access restored for ${member.email}`)}>
                  <RotateCcw className="text-ink-3" aria-hidden /> Restore access
                </DropdownMenuItem>
              )}
            </>
          ) : null}
        </DropdownMenuContent>
      </DropdownMenu>
    </li>
  );
}

/** Name, country and reporting currency of the current workspace. */
function WorkspaceSettings({ organization }: { organization: Organization }) {
  const { setOrganization } = useAudit();
  const [name, setName] = useState(organization.name);
  const [currency, setCurrency] = useState(organization.currency_code);
  const [country, setCountry] = useState(organization.country_code ?? "");
  const [saving, setSaving] = useState(false);

  const dirty =
    name.trim() !== organization.name || currency.trim().toUpperCase() !== organization.currency_code || country.trim().toUpperCase() !== (organization.country_code ?? "");
  const valid = name.trim().length > 0 && /^[A-Za-z]{3}$/.test(currency.trim()) && (country.trim() === "" || /^[A-Za-z]{2}$/.test(country.trim()));

  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!dirty || !valid || saving) return;
    setSaving(true);
    try {
      const updated = await api.updateOrganization({
        name: name.trim(),
        currency_code: currency.trim().toUpperCase(),
        country_code: country.trim() ? country.trim().toUpperCase() : null,
      });
      setOrganization(updated);
      setName(updated.name);
      setCurrency(updated.currency_code);
      setCountry(updated.country_code ?? "");
      toast.success("Workspace settings saved");
    } catch (failure) {
      toast.error("Couldn't save the workspace settings", { description: toApiError(failure).message });
    } finally {
      setSaving(false);
    }
  };

  const field =
    "h-9 w-full rounded-lg border border-line bg-surface-solid px-3 text-[13px] text-ink shadow-[var(--shadow-card)] placeholder:text-ink-3 transition-[border-color,box-shadow] focus:border-accent/60 focus:shadow-[0_0_0_3px_color-mix(in_oklab,var(--accent)_18%,transparent)] focus:outline-none";

  return (
    <section aria-labelledby="workspace-heading" className="glass rounded-xl px-4 py-4">
      <h2 id="workspace-heading" className="flex items-center gap-2 text-[15px] font-semibold tracking-[-0.01em]">
        <Building2 className="size-4 text-ink-3" aria-hidden /> Workspace settings
      </h2>
      <form onSubmit={(event) => void save(event)} className="mt-3 flex flex-col gap-3">
        <label className="flex flex-col gap-1.5">
          <span className="eyebrow">Name</span>
          <input value={name} maxLength={255} onChange={(event) => setName(event.target.value)} className={field} />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1.5">
            <span className="eyebrow">Currency</span>
            <input value={currency} maxLength={3} placeholder="PKR" onChange={(event) => setCurrency(event.target.value.toUpperCase())} className={cn(field, "figure uppercase")} />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="eyebrow">Country</span>
            <input value={country} maxLength={2} placeholder="PK" onChange={(event) => setCountry(event.target.value.toUpperCase())} className={cn(field, "figure uppercase")} />
          </label>
        </div>
        <p className="text-[11.5px] leading-relaxed text-ink-3">Currency is a 3-letter code (PKR, USD). Country is a 2-letter code (PK) and decides the tax-rate check.</p>
        <Button type="submit" variant="secondary" disabled={!dirty || !valid || saving}>
          {saving ? <LoaderCircle className="animate-spin" aria-hidden /> : <Check aria-hidden />}
          {saving ? "Saving…" : "Save changes"}
        </Button>
      </form>
    </section>
  );
}

function RolesGuide() {
  return (
    <section aria-labelledby="roles-heading" className="glass rounded-xl px-4 py-4">
      <h2 id="roles-heading" className="eyebrow">
        What each role can do
      </h2>
      <dl className="mt-3 flex flex-col gap-2.5">
        {ROLES.map((role) => (
          <div key={role.value} className="grid grid-cols-[76px_minmax(0,1fr)] gap-2">
            <dt className="text-[12.5px] font-medium text-ink">{role.label}</dt>
            <dd className="text-[12.5px] leading-snug text-ink-2">{role.summary}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-3.5 border-t border-line pt-3 text-[11.5px] leading-relaxed text-ink-3">
        Removing access signs the person out of this workspace on their next request. Their past approvals stay in the audit trail,
        and their own workspaces are not affected.
      </p>
    </section>
  );
}
