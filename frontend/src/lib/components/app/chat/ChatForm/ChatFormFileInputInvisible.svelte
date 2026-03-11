<script lang="ts">
	interface Props {
		class?: string;
		multiple?: boolean;
		onFileSelect?: (files: File[]) => void;
	}

	let { class: className = '', multiple = true, onFileSelect }: Props = $props();

	let fileInputElement: HTMLInputElement | undefined;
	let folderInputElement: HTMLInputElement | undefined;

	export function click() {
		fileInputElement?.click();
	}

	export function clickFolder() {
		folderInputElement?.click();
	}

	function handleFileSelect(event: Event) {
		const input = event.target as HTMLInputElement;
		if (input.files) {
			onFileSelect?.(Array.from(input.files));
			// Reset so the same file/folder can be re-selected
			input.value = '';
		}
	}
</script>

<input
	bind:this={fileInputElement}
	type="file"
	{multiple}
	onchange={handleFileSelect}
	class="hidden {className}"
/>
<input
	bind:this={folderInputElement}
	type="file"
	webkitdirectory
	onchange={handleFileSelect}
	class="hidden {className}"
/>
