<script lang="ts">
	import { onMount } from 'svelte';
	import { Scale, ChevronDown, X, Loader2 } from '@lucide/svelte';
	import * as DropdownMenu from '$lib/components/ui/dropdown-menu';
	import { cn } from '$lib/components/ui/utils';
	import { counselStore } from '$lib/stores/counsel.svelte';

	interface Props {
		class?: string;
		disabled?: boolean;
	}

	let { class: className = '', disabled = false }: Props = $props();

	let isOpen = $state(false);

	onMount(() => {
		counselStore.fetchCounsels();
	});

	function handleSelect(counsel: (typeof counselStore.counsels)[number]) {
		counselStore.selectForChat(counsel);
		isOpen = false;
	}

	function handleClear(e: MouseEvent) {
		e.stopPropagation();
		counselStore.clearChatSelection();
	}

	let selected = $derived(counselStore.selectedForChat);
	let counsels = $derived(counselStore.counsels);
	let loading = $derived(counselStore.counselsLoading);
</script>

<div class={cn('relative inline-flex items-center', className)}>
	<DropdownMenu.Root bind:open={isOpen}>
		<DropdownMenu.Trigger
			{disabled}
			onclick={(e: MouseEvent) => {
				e.preventDefault();
				e.stopPropagation();
			}}
		>
			<button
				type="button"
				class={cn(
					'inline-flex cursor-pointer items-center gap-1.5 rounded-sm px-1.5 py-1 text-xs transition hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60',
					selected
						? 'bg-amber-500/15 text-amber-600 dark:text-amber-400 hover:bg-amber-500/25'
						: 'bg-muted-foreground/10 text-muted-foreground'
				)}
				{disabled}
			>
				<Scale class="h-3.5 w-3.5" />

				{#if selected}
					<span class="max-w-[10rem] truncate font-medium">{selected.name.replace(/_/g, ' ')}</span>
				{:else}
					<span class="font-medium">Counsel</span>
				{/if}

				{#if loading}
					<Loader2 class="h-3 w-3 animate-spin" />
				{:else}
					<ChevronDown class="h-3 w-3" />
				{/if}
			</button>
		</DropdownMenu.Trigger>

		<DropdownMenu.Content align="end" class="w-72">
			{#if selected}
				<DropdownMenu.Item
					class="flex items-center gap-2 text-xs text-muted-foreground"
					onclick={() => counselStore.clearChatSelection()}
				>
					<X class="h-3.5 w-3.5" />
					Clear counsel (use model instead)
				</DropdownMenu.Item>
				<DropdownMenu.Separator />
			{/if}

			{#if counsels.length === 0}
				<div class="px-3 py-2 text-sm text-muted-foreground">
					{loading ? 'Loading counsels...' : 'No counsels available'}
				</div>
			{:else}
				{#each counsels as counsel (counsel.name)}
					{@const isSelected = selected?.name === counsel.name}
					<DropdownMenu.Item
						class={cn(
							'flex flex-col items-start gap-0.5 px-3 py-2',
							isSelected && 'bg-amber-500/10'
						)}
						onclick={() => handleSelect(counsel)}
					>
						<div class="flex w-full items-center gap-2">
							<Scale class="h-3.5 w-3.5 shrink-0 text-amber-500" />
							<span class="font-medium">{counsel.name.replace(/_/g, ' ')}</span>
							<span class="ml-auto text-xs text-muted-foreground">
								{counsel.members.length} experts
							</span>
						</div>
						<p class="pl-5.5 text-xs text-muted-foreground">{counsel.description}</p>
					</DropdownMenu.Item>
				{/each}
			{/if}
		</DropdownMenu.Content>
	</DropdownMenu.Root>

	{#if selected}
		<button
			type="button"
			class="ml-0.5 rounded-sm p-0.5 text-muted-foreground hover:text-foreground"
			onclick={handleClear}
			title="Clear counsel selection"
		>
			<X class="h-3 w-3" />
		</button>
	{/if}
</div>
