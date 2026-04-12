<script lang="ts">
	import { onMount } from 'svelte';
	import { counselStore } from '$lib/stores/counsel.svelte';
	import CounselSelector from './CounselSelector.svelte';
	import MemberCard from './MemberCard.svelte';
	import SynthesisPanel from './SynthesisPanel.svelte';
	import FollowUpInput from './FollowUpInput.svelte';
	import CounselHistory from './CounselHistory.svelte';

	let task = $state('');
	let taskInputEl: HTMLTextAreaElement | undefined = $state();
	let availableModels = $state<string[]>([]);

	let isRunning = $derived(
		counselStore.status === 'running_members' || counselStore.status === 'running_synthesis'
	);
	let waitingForMembers = $derived(counselStore.status === 'running_members');
	let canRun = $derived(
		task.trim().length > 0 && counselStore.selectedCounsel !== null && !isRunning
	);
	let isReadOnlyView = $derived(counselStore.viewingRunId !== null);
	let doneMemberCount = $derived(counselStore.memberResponses.filter((m) => m.done).length);
	let totalMemberCount = $derived(counselStore.memberResponses.length);
	let membersProgress = $derived(
		totalMemberCount > 0 ? Math.min(100, (doneMemberCount / totalMemberCount) * 100) : 0
	);

	async function loadModels() {
		try {
			const res = await fetch('/api/models');
			if (!res.ok) return;
			const data = await res.json();
			availableModels = (data.data ?? []).map((m: { id: string }) => m.id).filter(Boolean);
		} catch (e) {
			console.warn('[counsel] models fetch failed:', e);
		}
	}

	onMount(() => {
		counselStore.fetchCounsels();
		loadModels();
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

	function toggleLayout() {
		counselStore.setLayoutMode(counselStore.layoutMode === 'grid' ? 'columns' : 'grid');
	}
</script>

<div class="flex h-full">
	<CounselHistory />

	<div class="flex-1 overflow-y-auto">
		<div class="flex h-full flex-col gap-6 p-6">
			<!-- Header -->
			<div class="flex items-start justify-between gap-4">
				<div>
					<h1 class="text-2xl font-bold tracking-tight">⚖ Counsel</h1>
					<p class="mt-1 text-sm text-muted-foreground">
						Convene a panel of expert LLMs to tackle your task, then get a synthesized view from the
						chairperson.
					</p>
				</div>

				<div class="flex items-center gap-2">
					<button
						class="shrink-0 rounded-md border px-3 py-1.5 text-xs transition-colors hover:bg-accent"
						onclick={toggleLayout}
						title="Toggle layout"
					>
						{counselStore.layoutMode === 'grid' ? '☰ Columns' : '▦ Grid'}
					</button>
					{#if counselStore.status === 'done' || counselStore.status === 'error' || isReadOnlyView}
						<button
							class="shrink-0 rounded-md border px-3 py-1.5 text-sm transition-colors hover:bg-accent"
							onclick={handleReset}
						>
							New Session
						</button>
					{/if}
				</div>
			</div>

			{#if isReadOnlyView}
				<div class="rounded-lg border border-amber-500/30 bg-amber-500/5 px-4 py-2 text-xs text-amber-700 dark:text-amber-400">
					Viewing a past run (read-only). Click <strong>New Session</strong> to start fresh.
				</div>
			{/if}

			<!-- Task input + council selector -->
			{#if (counselStore.status === 'idle' || isRunning) && !isReadOnlyView}
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
								class="inline-flex items-center gap-2 rounded-md border border-destructive/40 px-4 py-2 text-sm font-medium text-destructive transition-colors hover:bg-destructive/10"
								onclick={handleStop}
							>
								⏹ Stop
							</button>
							<span class="text-sm text-muted-foreground">
								{counselStore.status === 'running_members'
									? `Council is deliberating… ${doneMemberCount}/${totalMemberCount} done`
									: 'Chairperson synthesizing…'}
							</span>
						{/if}
					</div>

					{#if isRunning && totalMemberCount > 0}
						<div class="h-1 w-full overflow-hidden rounded-full bg-muted">
							<div
								class="h-full rounded-full bg-primary transition-all duration-300"
								style="width: {counselStore.status === 'running_synthesis'
									? 100
									: membersProgress}%"
							></div>
						</div>
					{/if}
				</div>
			{:else}
				<div class="rounded-lg border bg-muted/40 px-4 py-3">
					<p class="text-xs font-medium uppercase tracking-wide text-muted-foreground">Task</p>
					<p class="mt-1 text-sm">{counselStore.task}</p>
				</div>
			{/if}

			<!-- Error banner -->
			{#if counselStore.error}
				<div class="rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive">
					<strong>Error:</strong>
					{counselStore.error}
				</div>
			{/if}

			<!-- Member cards -->
			{#if counselStore.memberResponses.length > 0}
				<div>
					<div class="mb-3 flex items-center justify-between">
						<h2 class="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
							Council Members
						</h2>
						<span class="text-xs text-muted-foreground">
							{doneMemberCount}/{totalMemberCount} ready
						</span>
					</div>
					<div
						class={counselStore.layoutMode === 'columns'
							? 'flex gap-4 overflow-x-auto pb-2'
							: 'grid gap-4 sm:grid-cols-2 lg:grid-cols-3'}
					>
						{#each counselStore.memberResponses as member}
							<div
								class={counselStore.layoutMode === 'columns' ? 'min-w-[320px] flex-1' : ''}
							>
								<MemberCard
									{member}
									running={isRunning || counselStore.status === 'running_synthesis'}
									{availableModels}
								/>
							</div>
						{/each}
					</div>
				</div>
			{/if}

			<!-- Synthesis panel -->
			{#if counselStore.memberResponses.length > 0 && counselStore.selectedCounsel}
				<SynthesisPanel
					tokens={counselStore.synthesisTokens}
					chairpersonModel={counselStore.selectedCounsel.chairperson.model}
					running={counselStore.status === 'running_synthesis'}
					{waitingForMembers}
					totalUsage={counselStore.totalUsage}
				/>
			{/if}

			<!-- Follow-up input -->
			{#if counselStore.status === 'done' && counselStore.lastRunId && !isReadOnlyView}
				<FollowUpInput />
			{/if}
		</div>
	</div>
</div>
