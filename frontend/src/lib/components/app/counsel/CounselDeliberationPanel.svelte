<script lang="ts">
	import { Scale, ChevronRight, Loader2, CheckCircle2, AlertCircle } from '@lucide/svelte';
	import { MarkdownContent } from '$lib/components/app';
	import type { CounselDeliberation, MemberResponse, CounselStatus } from '$lib/stores/counsel.svelte';

	interface Props {
		/** Persisted deliberation from message.extra (after streaming completes) */
		deliberation?: CounselDeliberation;
		/** Live member responses during streaming */
		liveMembers?: MemberResponse[];
		/** Live status during streaming */
		liveStatus?: CounselStatus;
	}

	let { deliberation, liveMembers, liveStatus }: Props = $props();

	let isLive = $derived(liveStatus && liveStatus !== 'idle' && liveStatus !== 'done');

	// Use live data when streaming, persisted data otherwise
	let counselName = $derived(deliberation?.counselName ?? '');
	let members = $derived.by(() => {
		if (isLive && liveMembers) {
			return liveMembers.map((m) => ({
				role: m.role,
				model: m.model,
				content: m.tokens.join(''),
				done: m.done,
				error: m.error
			}));
		}
		if (deliberation) {
			return deliberation.members.map((m) => ({
				role: m.role,
				model: m.model,
				content: m.content,
				done: true,
				error: m.error
			}));
		}
		return [];
	});

	let memberCount = $derived(members.length);
	let completedCount = $derived(members.filter((m) => m.done).length);
	let statusText = $derived.by(() => {
		if (liveStatus === 'running_members') {
			return `${completedCount}/${memberCount} experts done`;
		}
		if (liveStatus === 'running_synthesis') {
			return 'Chairperson synthesizing...';
		}
		return `${memberCount} experts`;
	});

	let isOpen = $state(false);
</script>

<div class="counsel-deliberation mb-3 rounded-lg border border-amber-500/20 bg-amber-500/5">
	<button
		type="button"
		class="flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition-colors hover:bg-amber-500/10"
		onclick={() => (isOpen = !isOpen)}
	>
		<ChevronRight
			class="h-3.5 w-3.5 shrink-0 text-amber-500 transition-transform {isOpen
				? 'rotate-90'
				: ''}"
		/>
		<Scale class="h-3.5 w-3.5 shrink-0 text-amber-500" />
		<span class="font-medium text-amber-600 dark:text-amber-400">
			Council{counselName ? `: ${counselName.replace(/_/g, ' ')}` : ''}
		</span>
		<span class="ml-auto flex items-center gap-1.5 text-muted-foreground">
			{#if isLive}
				<Loader2 class="h-3 w-3 animate-spin text-amber-500" />
			{/if}
			{statusText}
		</span>
	</button>

	{#if isOpen}
		<div class="border-t border-amber-500/20 px-3 py-2">
			<div class="grid gap-2">
				{#each members as member (member.role)}
					<div class="rounded-md border border-border/50 bg-background/50 px-3 py-2">
						<div class="mb-1 flex items-center gap-2 text-xs">
							{#if member.error}
								<AlertCircle class="h-3.5 w-3.5 text-destructive" />
							{:else if member.done}
								<CheckCircle2 class="h-3.5 w-3.5 text-green-500" />
							{:else}
								<Loader2 class="h-3.5 w-3.5 animate-spin text-amber-500" />
							{/if}
							<span class="font-medium">{member.role}</span>
							<span class="text-muted-foreground">({member.model})</span>
						</div>
						{#if member.error}
							<p class="text-xs text-destructive">{member.error}</p>
						{:else if member.content}
							<div class="max-h-48 overflow-y-auto text-sm">
								<MarkdownContent content={member.content} />
							</div>
						{:else if !member.done}
							<p class="text-xs text-muted-foreground italic">Thinking...</p>
						{/if}
					</div>
				{/each}
			</div>
		</div>
	{/if}
</div>
