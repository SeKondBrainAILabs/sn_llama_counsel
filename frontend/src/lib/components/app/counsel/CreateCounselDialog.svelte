<script lang="ts">
	import * as Dialog from '$lib/components/ui/dialog';
	import { Textarea } from '$lib/components/ui/textarea';
	import { Button } from '$lib/components/ui/button';
	import { counselStore, type CounselConfig } from '$lib/stores/counsel.svelte';
	import { Loader2, Sparkles } from '@lucide/svelte';
	import { toast } from 'svelte-sonner';

	interface Props {
		open: boolean;
		onCreated?: (counsel: CounselConfig) => void;
	}

	let { open = $bindable(), onCreated }: Props = $props();
	let description = $state('');
	let loading = $derived(counselStore.createLoading);
	let error = $derived(counselStore.createError);

	async function handleCreate() {
		if (!description.trim() || loading) return;
		const result = await counselStore.createCounsel(description.trim());
		if (result) {
			counselStore.selectForChat(result);
			onCreated?.(result);
			toast.success(`Counsel "${result.name.replace(/_/g, ' ')}" created!`, { duration: 4000 });
			description = '';
			open = false;
		}
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
			e.preventDefault();
			handleCreate();
		}
	}
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="max-w-lg">
		<Dialog.Header>
			<Dialog.Title class="flex items-center gap-2">
				<Sparkles class="h-5 w-5 text-amber-500" />
				Create Counsel
			</Dialog.Title>
			<Dialog.Description>
				Describe what you want the counsel to do. A chairperson LLM will design
				the panel of experts for you.
			</Dialog.Description>
		</Dialog.Header>

		<div class="space-y-4 py-4">
			<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
			<Textarea
				bind:value={description}
				placeholder="e.g. A panel that reviews Python code for security, performance, and readability..."
				rows={4}
				disabled={loading}
				onkeydown={handleKeydown}
			/>
			{#if error}
				<p class="text-sm text-destructive">{error}</p>
			{/if}
			<p class="text-xs text-muted-foreground">
				Tip: Be specific about the perspectives you want. Press ⌘+Enter to create.
			</p>
		</div>

		<Dialog.Footer>
			<Button variant="outline" onclick={() => (open = false)} disabled={loading}>
				Cancel
			</Button>
			<Button onclick={handleCreate} disabled={!description.trim() || loading}>
				{#if loading}
					<Loader2 class="mr-2 h-4 w-4 animate-spin" />
					Designing counsel...
				{:else}
					Create
				{/if}
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
