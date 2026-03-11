import { isSvgMimeType, svgBase64UrlToPngDataURL } from './svg-to-png';
import { isWebpMimeType, webpBase64UrlToPngDataURL } from './webp-to-png';
import { FileTypeCategory } from '$lib/enums';
import { modelsStore } from '$lib/stores/models.svelte';
import { settingsStore } from '$lib/stores/settings.svelte';
import { toast } from 'svelte-sonner';
import { getFileTypeCategory } from '$lib/utils';
import { convertPDFToText } from './pdf-processing';

/**
 * Read a file as a data URL (base64 encoded)
 * @param file - The file to read
 * @returns Promise resolving to the data URL string
 */
function readFileAsDataURL(file: File): Promise<string> {
	return new Promise((resolve, reject) => {
		const reader = new FileReader();
		reader.onload = () => resolve(reader.result as string);
		reader.onerror = () => reject(reader.error);
		reader.readAsDataURL(file);
	});
}

/**
 * Read a file as UTF-8 text
 * @param file - The file to read
 * @returns Promise resolving to the text content
 */
function readFileAsUTF8(file: File): Promise<string> {
	return new Promise((resolve, reject) => {
		const reader = new FileReader();
		reader.onload = () => resolve(reader.result as string);
		reader.onerror = () => reject(reader.error);
		reader.readAsText(file);
	});
}

// ── Folder attachment filtering ──────────────────────────────────────────

/** Directories and file patterns to skip when attaching folders. */
const FOLDER_IGNORE_PATTERNS = [
	'node_modules/',
	'.git/',
	'.svn/',
	'__pycache__/',
	'.next/',
	'.cache/',
	'dist/',
	'build/',
	'.DS_Store',
	'package-lock.json',
	'yarn.lock',
	'pnpm-lock.yaml',
	'.env',
	'.wasm',
	'.venv/',
	'.tox/',
	'coverage/',
	'.nyc_output/'
];

const BINARY_EXTENSIONS = new Set([
	'.png',
	'.jpg',
	'.jpeg',
	'.gif',
	'.bmp',
	'.ico',
	'.webp',
	'.svg',
	'.mp3',
	'.wav',
	'.ogg',
	'.mp4',
	'.mov',
	'.avi',
	'.mkv',
	'.zip',
	'.tar',
	'.gz',
	'.rar',
	'.7z',
	'.exe',
	'.dll',
	'.so',
	'.dylib',
	'.o',
	'.a',
	'.woff',
	'.woff2',
	'.ttf',
	'.eot',
	'.otf',
	'.pyc',
	'.pyo',
	'.class',
	'.gguf',
	'.bin',
	'.weights'
]);

/** Max files to keep from a single folder upload (to avoid context overflow). */
const MAX_FOLDER_FILES = 50;

/** Max single-file size (500KB). */
const MAX_FILE_SIZE = 500 * 1024;

/**
 * Filter folder-selected files: skip ignored dirs, binaries, large/empty files.
 * Returns at most MAX_FOLDER_FILES sorted by path.
 */
export function filterFolderFiles(files: File[]): File[] {
	const filtered = files.filter((file) => {
		const path = file.webkitRelativePath || file.name;

		// Skip ignored directories/files
		if (FOLDER_IGNORE_PATTERNS.some((pattern) => path.includes(pattern))) {
			return false;
		}

		// Skip binary files by extension
		const dotIdx = path.lastIndexOf('.');
		if (dotIdx !== -1) {
			const ext = path.slice(dotIdx).toLowerCase();
			if (BINARY_EXTENSIONS.has(ext)) {
				return false;
			}
		}

		// Skip oversized files
		if (file.size > MAX_FILE_SIZE) {
			return false;
		}

		// Skip empty files
		if (file.size === 0) {
			return false;
		}

		return true;
	});

	// Sort by path for consistent ordering, cap at limit
	filtered.sort((a, b) =>
		(a.webkitRelativePath || a.name).localeCompare(b.webkitRelativePath || b.name)
	);

	if (filtered.length > MAX_FOLDER_FILES) {
		console.warn(
			`[folder] Capped from ${filtered.length} to ${MAX_FOLDER_FILES} files`
		);
		return filtered.slice(0, MAX_FOLDER_FILES);
	}

	return filtered;
}

/**
 * Process uploaded files into ChatUploadedFile format with previews and content
 *
 * This function processes various file types and generates appropriate previews:
 * - Images: Base64 data URLs with format normalization (SVG/WebP → PNG)
 * - Text files: UTF-8 content extraction
 * - PDFs: Metadata only (processed later in conversion pipeline)
 * - Audio: Base64 data URLs for preview
 *
 * @param files - Array of File objects to process
 * @returns Promise resolving to array of ChatUploadedFile objects
 */
export async function processFilesToChatUploaded(
	files: File[],
	activeModelId?: string
): Promise<ChatUploadedFile[]> {
	const results: ChatUploadedFile[] = [];

	for (const file of files) {
		const id = Date.now().toString() + Math.random().toString(36).substr(2, 9);
		const base: ChatUploadedFile = {
			id,
			name: file.name,
			size: file.size,
			type: file.type,
			file
		};

		try {
			if (getFileTypeCategory(file.type) === FileTypeCategory.IMAGE) {
				let preview = await readFileAsDataURL(file);

				// Normalize SVG and WebP to PNG in previews
				if (isSvgMimeType(file.type)) {
					try {
						preview = await svgBase64UrlToPngDataURL(preview);
					} catch (err) {
						console.error('Failed to convert SVG to PNG:', err);
					}
				} else if (isWebpMimeType(file.type)) {
					try {
						preview = await webpBase64UrlToPngDataURL(preview);
					} catch (err) {
						console.error('Failed to convert WebP to PNG:', err);
					}
				}

				results.push({ ...base, preview });
			} else if (getFileTypeCategory(file.type) === FileTypeCategory.PDF) {
				// Extract text content from PDF for preview
				try {
					const textContent = await convertPDFToText(file);
					results.push({ ...base, textContent });
				} catch (err) {
					console.warn('Failed to extract text from PDF, adding without content:', err);
					results.push(base);
				}

				// Show suggestion toast if vision model is available but PDF as image is disabled
				const hasVisionSupport = activeModelId
					? modelsStore.modelSupportsVision(activeModelId)
					: false;
				const currentConfig = settingsStore.config;
				if (hasVisionSupport && !currentConfig.pdfAsImage) {
					toast.info(`You can enable parsing PDF as images with vision models.`, {
						duration: 8000,
						action: {
							label: 'Enable PDF as Images',
							onClick: () => {
								settingsStore.updateConfig('pdfAsImage', true);
								toast.success('PDF parsing as images enabled!', {
									duration: 3000
								});
							}
						}
					});
				}
			} else if (getFileTypeCategory(file.type) === FileTypeCategory.AUDIO) {
				// Generate preview URL for audio files
				const preview = await readFileAsDataURL(file);
				results.push({ ...base, preview });
			} else {
				// Fallback: treat unknown files as text
				try {
					const textContent = await readFileAsUTF8(file);
					results.push({ ...base, textContent });
				} catch (err) {
					console.warn('Failed to read file as text, adding without content:', err);
					results.push(base);
				}
			}
		} catch (error) {
			console.error('Error processing file', file.name, error);
			results.push(base);
		}
	}

	return results;
}
