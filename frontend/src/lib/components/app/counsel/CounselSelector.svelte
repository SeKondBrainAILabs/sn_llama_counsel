<script lang="ts">
	import type { CounselConfig } from '$lib/stores/counsel.svelte';

	interface Props {
		counsels: CounselConfig[];
		selected: CounselConfig | null;
		loading: boolean;
		autoSelectLoading: boolean;
		task: string;
		onSelect: (counsel: CounselConfig) => void;
		onAutoSelect: () => void;
	}

	let {
		counsels,
		selected,
		loading,
		autoSelectLoading,
		task,
		onSelect,
		onAutoSelect
	}: Props = $props();
</script>

<div class="flex items-center gap-2 flex-wrap">
	{#if loading}
		<span class="text-sm text-muted-foreground">Loading councils…</span>
	{:else}
		<select
			class="rounded-md border border-input bg-background px-3 py-1.5 text-sm shadow-sm focus:outline-none focus:ring-1 focus:ring-ring"
			onchange={(e) => {
				const name = (e.target as HTMLSelectElement).value;
				const found = counsels.find((c) => c.name === name);
				if (found) onSelect(found);
			}}
		>
			{#each counsels as c}
				<option value={c.name} selected={selected?.name === c.name}>{c.description || c.name}</option>
			{/each}
		</select>

		<button
			class="inline-flex items-center gap-1.5 rounded-md border border-input bg-background px-3 py-1.5 text-sm shadow-sm transition-colors hover:bg-accent hover:text-accent-foreground disabled:cursor-not-allowed disabled:opacity-50"
			onclick={onAutoSelect}
			disabled={autoSelectLoading || !task.trim()}
			title="Let the chairperson LLM pick the best council for your task"
		>
			{#if autoSelectLoading}
				<span class="inline-block h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent"></span>
			{:else}
				✨
			{/if}
			Auto-select
		</button>
	{/if}

	{#if selected}
		<span class="text-xs text-muted-foreground">
			{selected.members.length} members · chair: <span class="font-medium">{selected.chairperson.model}</span>
		</span>
	{/if}
</div>
