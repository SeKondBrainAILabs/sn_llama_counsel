<script lang="ts">
	import type { MemberResponse } from '$lib/stores/counsel.svelte';
	import { counselStore } from '$lib/stores/counsel.svelte';

	interface Props {
		member: MemberResponse;
		running: boolean;
		availableModels?: string[];
	}

	let { member, running, availableModels = [] }: Props = $props();

	const ROLE_ICONS: Record<string, string> = {
		'Security Expert': '🔐',
		'Architecture Expert': '🏗',
		'Code Quality': '✨',
		Analyst: '🔬',
		"Devil's Advocate": '😈',
		Synthesizer: '🧩',
		Creative: '🎨',
		Critical: '🎯',
		Editorial: '📝',
		Expansive: '🌐'
	};

	let icon = $derived(ROLE_ICONS[member.role] ?? '🤖');
	let text = $derived(member.tokens.join(''));
	let isStreaming = $derived(running && !member.done);
	let hasOverride = $derived(counselStore.hasOverride(member.role));
	let override = $derived(counselStore.memberOverrides[member.role]);

	let overrideOpen = $state(false);

	function elapsedLabel(m: MemberResponse): string {
		if (m.startedAt === null) return '';
		const end = m.finishedAt ?? Date.now();
		const ms = end - m.startedAt;
		if (ms < 1000) return `${ms}ms`;
		return `${(ms / 1000).toFixed(1)}s`;
	}

	// Tick so the elapsed label updates while streaming
	let _tick = $state(0);
	$effect(() => {
		if (!isStreaming) return;
		const id = setInterval(() => (_tick = _tick + 1), 200);
		return () => clearInterval(id);
	});

	function onModelChange(e: Event) {
		const v = (e.target as HTMLSelectElement).value;
		counselStore.setOverride(member.role, v ? { model: v } : null);
	}

	function onTempChange(e: Event) {
		const raw = (e.target as HTMLInputElement).value;
		const v = raw === '' ? undefined : parseFloat(raw);
		counselStore.setOverride(member.role, { temperature: v });
	}

	function clearOverride() {
		counselStore.setOverride(member.role, null);
		overrideOpen = false;
	}
</script>

<div
	class="flex flex-col rounded-lg border bg-card shadow-sm transition-all duration-200 {member.error
		? 'border-destructive/50'
		: member.done
			? 'border-green-500/20'
			: isStreaming
				? 'border-primary/30 shadow-primary/10'
				: ''}"
>
	<!-- Header -->
	<div class="flex items-center gap-2 border-b px-4 py-3">
		<span class="text-lg leading-none">{icon}</span>
		<div class="flex-1 min-w-0">
			<p class="truncate font-semibold text-sm">
				{member.role}
				{#if hasOverride}
					<span
						class="ml-1 rounded bg-amber-500/10 px-1 py-0 text-[9px] font-medium text-amber-600 dark:text-amber-400"
						title="Override active">override</span
					>
				{/if}
			</p>
			<p class="truncate text-xs text-muted-foreground">{member.model}</p>
		</div>
		{#if isStreaming}
			<span
				class="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary"
			>
				<span class="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-primary"></span>
				Thinking
			</span>
		{:else if member.error}
			<span class="rounded-full bg-destructive/10 px-2 py-0.5 text-[10px] font-medium text-destructive">
				Error
			</span>
		{:else if member.done}
			<span
				class="rounded-full bg-green-500/10 px-2 py-0.5 text-[10px] font-medium text-green-600 dark:text-green-400"
			>
				Done
			</span>
		{/if}

		<!-- Override menu toggle -->
		<button
			class="rounded p-1 text-muted-foreground hover:bg-accent"
			aria-label="Member options"
			onclick={() => (overrideOpen = !overrideOpen)}
			disabled={running}
		>
			⋯
		</button>
	</div>

	{#if overrideOpen}
		<div class="border-b bg-muted/30 px-4 py-2 text-xs">
			<label class="mb-2 block">
				<span class="block text-[10px] font-medium uppercase text-muted-foreground">Model</span>
				<select
					class="mt-0.5 w-full rounded border bg-background px-2 py-1 text-xs"
					value={override?.model ?? ''}
					onchange={onModelChange}
				>
					<option value="">— default ({member.model}) —</option>
					{#each availableModels as m}
						<option value={m}>{m}</option>
					{/each}
				</select>
			</label>
			<label class="mb-2 block">
				<span class="block text-[10px] font-medium uppercase text-muted-foreground">
					Temperature
				</span>
				<input
					type="number"
					step="0.05"
					min="0"
					max="2"
					placeholder="default"
					value={override?.temperature ?? ''}
					onchange={onTempChange}
					class="mt-0.5 w-full rounded border bg-background px-2 py-1 text-xs"
				/>
			</label>
			<div class="flex justify-end">
				<button class="text-[11px] text-muted-foreground underline" onclick={clearOverride}>
					Clear override
				</button>
			</div>
		</div>
	{/if}

	<!-- Content -->
	<div class="flex-1 px-4 py-3 min-h-[120px] max-h-[320px] overflow-y-auto">
		{#if member.error}
			<p class="text-sm text-destructive">{member.error}</p>
		{:else if text}
			<p class="whitespace-pre-wrap text-sm leading-relaxed">{text}{isStreaming ? '▍' : ''}</p>
		{:else if isStreaming}
			<p class="text-sm text-muted-foreground italic">Waiting for response…</p>
		{:else}
			<p class="text-sm text-muted-foreground italic">Not started</p>
		{/if}
	</div>

	<!-- Footer: timing + tokens -->
	{#if member.startedAt !== null || member.usage}
		<div class="flex items-center justify-between gap-3 border-t px-4 py-2 text-[11px] text-muted-foreground">
			<span>{elapsedLabel(member)}{_tick === -1 ? '' : ''}</span>
			{#if member.usage}
				<span>
					{member.usage.prompt.toLocaleString()} in / {member.usage.completion.toLocaleString()} out
				</span>
			{/if}
		</div>
	{/if}
</div>
