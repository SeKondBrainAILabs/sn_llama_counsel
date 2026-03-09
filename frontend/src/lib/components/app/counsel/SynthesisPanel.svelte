<script lang="ts">
	interface Props {
		tokens: string[];
		chairpersonModel: string;
		running: boolean;
		waitingForMembers: boolean;
	}

	let { tokens, chairpersonModel, running, waitingForMembers }: Props = $props();

	let text = $derived(tokens.join(''));
	let isStreaming = $derived(running && !waitingForMembers);
</script>

<div class="rounded-lg border bg-card shadow-sm {isStreaming ? 'border-amber-500/30' : text ? 'border-green-500/20' : ''}">
	<!-- Header -->
	<div class="flex items-center gap-2 border-b px-4 py-3">
		<span class="text-lg leading-none">⚖</span>
		<div class="flex-1 min-w-0">
			<p class="font-semibold text-sm">Chairperson Synthesis</p>
			<p class="truncate text-xs text-muted-foreground">{chairpersonModel}</p>
		</div>
		{#if waitingForMembers}
			<span class="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
				Waiting for council…
			</span>
		{:else if isStreaming}
			<span class="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
				<span class="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-amber-500"></span>
				Synthesizing
			</span>
		{:else if text}
			<span class="rounded-full bg-green-500/10 px-2 py-0.5 text-[10px] font-medium text-green-600 dark:text-green-400">Done</span>
		{/if}
	</div>

	<!-- Content -->
	<div class="px-4 py-4 min-h-[120px] max-h-[480px] overflow-y-auto">
		{#if text}
			<p class="whitespace-pre-wrap text-sm leading-relaxed">{text}{isStreaming ? '▍' : ''}</p>
		{:else if isStreaming}
			<p class="text-sm text-muted-foreground italic">Generating synthesis…</p>
		{:else if waitingForMembers}
			<p class="text-sm text-muted-foreground italic">
				The chairperson will synthesize all council responses once everyone has finished.
			</p>
		{:else}
			<p class="text-sm text-muted-foreground italic">
				Synthesis will appear here after the council responds.
			</p>
		{/if}
	</div>
</div>
