<script lang="ts">
	import { onMount } from 'svelte';
	import { counselStore } from '$lib/stores/counsel.svelte';
	import CounselSelector from './CounselSelector.svelte';
	import MemberCard from './MemberCard.svelte';
	import SynthesisPanel from './SynthesisPanel.svelte';

	let task = $state('');
	let taskInputEl: HTMLTextAreaElement | undefined = $state();

	let isRunning = $derived(
		counselStore.status === 'running_members' || counselStore.status === 'running_synthesis'
	);
	let waitingForMembers = $derived(counselStore.status === 'running_members');
	let canRun = $derived(task.trim().length > 0 && counselStore.selectedCounsel !== null && !isRunning);

	onMount(() => {
		counselStore.fetchCounsels();
		taskInputEl?.focus();
	});

	function handleKeydown(e: KeyboardEvent) {
		if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && canRun) {
			e.preventDefault();
			handleRun();
		}
	}

	function handleRun() {
		if (!canRun || !counselStore.selectedCounsel) return;
		counselStore.run(task, counselStore.selectedCounsel);
	}

	function handleStop() {
		counselStore.stop();
	}

	function handleReset() {
		counselStore.reset();
		task = '';
		taskInputEl?.focus();
	}

	function handleAutoSelect() {
		counselStore.autoSelect(task);
	}
</script>

<div class="flex h-full flex-col gap-6 overflow-y-auto p-6">
	<!-- Header -->
	<div class="flex items-start justify-between gap-4">
		<div>
			<h1 class="text-2xl font-bold tracking-tight">⚖ Counsel</h1>
			<p class="mt-1 text-sm text-muted-foreground">
				Convene a panel of expert LLMs to tackle your task, then get a synthesized view from the chairperson.
			</p>
		</div>

		{#if counselStore.status === 'done' || counselStore.status === 'error'}
			<button
				class="shrink-0 rounded-md border px-3 py-1.5 text-sm transition-colors hover:bg-accent"
				onclick={handleReset}
			>
				New Session
			</button>
		{/if}
	</div>

	<!-- Task input + council selector -->
	{#if counselStore.status === 'idle' || isRunning}
		<div class="flex flex-col gap-3 rounded-lg border bg-card p-4 shadow-sm">
			<CounselSelector
				counsels={counselStore.counsels}
				selected={counselStore.selectedCounsel}
				loading={counselStore.counselsLoading}
				autoSelectLoading={counselStore.autoSelectLoading}
				{task}
				onSelect={(c) => (counselStore.selectedCounsel = c)}
				onAutoSelect={handleAutoSelect}
			/>

			<textarea
				bind:this={taskInputEl}
				bind:value={task}
				onkeydown={handleKeydown}
				placeholder="Describe your task or question… (⌘↵ to convene)"
				rows={4}
				disabled={isRunning}
				class="w-full resize-none rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-50"
			></textarea>

			<div class="flex items-center gap-3">
				{#if !isRunning}
					<button
						class="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
						onclick={handleRun}
						disabled={!canRun}
					>
						▶ Convene Council
					</button>
				{:else}
					<button
						class="inline-flex items-center gap-2 rounded-md border px-4 py-2 text-sm font-medium transition-colors hover:bg-accent"
						onclick={handleStop}
					>
						⏹ Stop
					</button>
					<span class="text-sm text-muted-foreground">
						{counselStore.status === 'running_members' ? 'Council is deliberating…' : 'Chairperson synthesizing…'}
					</span>
				{/if}
			</div>
		</div>
	{:else}
		<!-- Show task summary after done/error -->
		<div class="rounded-lg border bg-muted/40 px-4 py-3">
			<p class="text-xs font-medium uppercase tracking-wide text-muted-foreground">Task</p>
			<p class="mt-1 text-sm">{counselStore.task}</p>
		</div>
	{/if}

	<!-- Error banner -->
	{#if counselStore.error}
		<div class="rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive">
			<strong>Error:</strong> {counselStore.error}
		</div>
	{/if}

	<!-- Member cards (shown once running starts) -->
	{#if counselStore.memberResponses.length > 0}
		<div>
			<h2 class="mb-3 text-sm font-semibold uppercase tracking-wide text-muted-foreground">Council Members</h2>
			<div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
				{#each counselStore.memberResponses as member}
					<MemberCard {member} running={isRunning || counselStore.status === 'running_synthesis'} />
				{/each}
			</div>
		</div>
	{/if}

	<!-- Synthesis panel (shown once members start) -->
	{#if counselStore.memberResponses.length > 0 && counselStore.selectedCounsel}
		<SynthesisPanel
			tokens={counselStore.synthesisTokens}
			chairpersonModel={counselStore.selectedCounsel.chairperson.model}
			running={counselStore.status === 'running_synthesis'}
			{waitingForMembers}
		/>
	{/if}
</div>
