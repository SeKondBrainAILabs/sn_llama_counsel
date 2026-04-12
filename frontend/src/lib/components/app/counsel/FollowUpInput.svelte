<script lang="ts">
	import { counselStore } from '$lib/stores/counsel.svelte';

	let followUp = $state('');
	let submitting = $state(false);

	async function handleSubmit() {
		if (!followUp.trim() || submitting) return;
		submitting = true;
		try {
			await counselStore.followUp(followUp);
			followUp = '';
		} finally {
			submitting = false;
		}
	}

	function handleKeydown(e: KeyboardEvent) {
		if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
			e.preventDefault();
			handleSubmit();
		}
	}
</script>

<div class="rounded-lg border bg-card p-3 shadow-sm">
	<p class="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
		Ask the council to go deeper
	</p>
	<textarea
		bind:value={followUp}
		onkeydown={handleKeydown}
		placeholder="Refine, clarify, or follow up on the synthesis… (⌘↵ to send)"
		rows={3}
		disabled={submitting}
		class="w-full resize-none rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-50"
	></textarea>
	<div class="mt-2 flex items-center justify-end">
		<button
			class="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground shadow transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
			onclick={handleSubmit}
			disabled={!followUp.trim() || submitting}
		>
			{submitting ? '…' : '↳'} Continue
		</button>
	</div>
</div>
