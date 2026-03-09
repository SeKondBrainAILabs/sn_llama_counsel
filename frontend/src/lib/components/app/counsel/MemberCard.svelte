<script lang="ts">
	import type { MemberResponse } from '$lib/stores/counsel.svelte';

	interface Props {
		member: MemberResponse;
		running: boolean;
	}

	let { member, running }: Props = $props();

	const ROLE_ICONS: Record<string, string> = {
		'Security Expert': '🔐',
		'Architecture Expert': '🏗',
		'Code Quality': '✨',
		Analyst: '🔬',
		'Devil\'s Advocate': '😈',
		Synthesizer: '🧩',
		Creative: '🎨',
		Critical: '🎯',
		Editorial: '📝',
		Expansive: '🌐'
	};

	let icon = $derived(ROLE_ICONS[member.role] ?? '🤖');
	let text = $derived(member.tokens.join(''));
	let isStreaming = $derived(running && !member.done);
</script>

<div
	class="flex flex-col rounded-lg border bg-card shadow-sm transition-all duration-200 {member.error ? 'border-destructive/50' : member.done ? 'border-green-500/20' : isStreaming ? 'border-primary/30 shadow-primary/10' : ''}"
>
	<!-- Header -->
	<div class="flex items-center gap-2 border-b px-4 py-3">
		<span class="text-lg leading-none">{icon}</span>
		<div class="flex-1 min-w-0">
			<p class="truncate font-semibold text-sm">{member.role}</p>
			<p class="truncate text-xs text-muted-foreground">{member.model}</p>
		</div>
		{#if isStreaming}
			<span class="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
				<span class="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-primary"></span>
				Thinking
			</span>
		{:else if member.error}
			<span class="rounded-full bg-destructive/10 px-2 py-0.5 text-[10px] font-medium text-destructive">Error</span>
		{:else if member.done}
			<span class="rounded-full bg-green-500/10 px-2 py-0.5 text-[10px] font-medium text-green-600 dark:text-green-400">Done</span>
		{/if}
	</div>

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
</div>
