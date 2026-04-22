export type ChatPdfExportMode = 'stylized' | 'compact';

type ExportChatPdfOptions = {
	sourceElement: HTMLElement;
	title?: string | null;
	mode?: ChatPdfExportMode;
	darkMode?: boolean;
};

type RgbaColor = {
	r: number;
	g: number;
	b: number;
	a: number;
};

type PageSlice = {
	offsetY: number;
	sliceHeight: number;
};

type BlockRange = {
	top: number;
	bottom: number;
	height: number;
	hasContentImage?: boolean;
};

type AtomicRange = {
	top: number;
	bottom: number;
	height: number;
	left?: number;
	right?: number;
	width?: number;
	image?: HTMLImageElement;
};

const PDF_PAGE_WIDTH_MM = 210;
const PDF_PAGE_HEIGHT_MM = 297;
const PDF_PAGE_TOP_MARGIN_MM = 6;
const PDF_PAGE_BOTTOM_MARGIN_MM = 2;
const PAGE_EDGE_PADDING_PX = 18;
const MESSAGE_HEAD_KEEP_WITH_BODY_PX = 150;
const MESSAGE_FIRST_BLOCK_KEEP_PX = 280;
const SMALL_TEXT_MESSAGE_MAX_HEIGHT_RATIO = 0.35;
const MESSAGE_CROSSING_THRESHOLD_CSS_PX = 180;
const MIN_NEAR_PAGE_IMAGE_SHRINK_RATIO = 0.78;
const MAX_PAGE_SLICE_PLAN_PASSES = 12;
const MAX_BREAK_NORMALIZATION_PASSES = 6;
const IMAGE_BREAK_SAFETY_PX = 24;
const CANVAS_IMAGE_POSITION_SEARCH_PX = 180;
const CANVAS_IMAGE_ROW_THRESHOLD_RATIO = 0.18;
const CANVAS_IMAGE_MIN_ROW_PIXELS = 24;
const CANVAS_IMAGE_EDGE_ROW_THRESHOLD_RATIO = 0.01;
const CANVAS_IMAGE_MIN_EDGE_ROW_PIXELS = 4;
const CANVAS_IMAGE_EDGE_GAP_PX = 3;
const MIN_PAGE_FILL_RATIO = 0.62;
const PREFERRED_MESSAGE_FILL_RATIO = 0.52;
const MIN_SLICE_HEIGHT_RATIO = 0.35;
const ATOMIC_BLOCK_MAX_HEIGHT_RATIO = 0.92;
const KEEP_TOGETHER_MAX_HEIGHT_RATIO = 0.22;
const CONTENT_IMAGE_SELECTOR = [
	'[id^="message-"] img[data-cy="image"]',
	'[id^="message-"] img[alt="Output"]'
].join(', ');
const PRIMARY_BREAK_SELECTOR = ['.pdf-export-header', '[id^="message-"]'].join(', ');
const SECONDARY_BREAK_SELECTOR = [
	'[id^="message-"] p',
	'[id^="message-"] ul',
	'[id^="message-"] ol',
	'[id^="message-"] li',
	'[id^="message-"] h1',
	'[id^="message-"] h2',
	'[id^="message-"] h3',
	'[id^="message-"] h4',
	'[id^="message-"] h5',
	'[id^="message-"] h6',
	'[id^="message-"] .status-description',
	'[id^="message-"] .message-outline-toolbar-row'
].join(', ');
const ATOMIC_BLOCK_SELECTOR = [
	'[id^="message-"] pre',
	'[id^="message-"] blockquote',
	'[id^="message-"] table',
	'[id^="message-"] details',
	'[id^="message-"] figure',
	'[id^="message-"] [class*="group/codeblock"]'
].join(', ');
const KEEP_TOGETHER_SELECTOR = [
	'[id^="message-"] .status-description',
	'[id^="message-"] .message-outline-toolbar-row',
	'[id^="message-"] h1',
	'[id^="message-"] h2',
	'[id^="message-"] h3',
	'[id^="message-"] h4',
	'[id^="message-"] h5',
	'[id^="message-"] h6',
	'[id^="message-"] p',
	'[id^="message-"] ul',
	'[id^="message-"] ol',
	'[id^="message-"] li'
].join(', ');

const MODE_CONFIG: Record<
	ChatPdfExportMode,
	{
		width: number;
		scale: number;
		quality: number;
		backgroundColor: (darkMode: boolean) => string;
	}
> = {
	stylized: {
		width: 820,
		scale: 2,
		quality: 0.78,
		backgroundColor: (darkMode) => (darkMode ? '#020617' : '#ffffff')
	},
	compact: {
		width: 760,
		scale: 1.25,
		quality: 0.72,
		backgroundColor: () => '#ffffff'
	}
};

const waitForNextFrame = () =>
	new Promise<void>((resolve) => {
		requestAnimationFrame(() => resolve());
	});

const waitForStableLayout = async () => {
	await waitForNextFrame();
	await waitForNextFrame();

	if (document.fonts?.ready) {
		try {
			await document.fonts.ready;
		} catch {
			// ignore font loading errors and let html2canvas continue
		}
	}

	await new Promise((resolve) => setTimeout(resolve, 60));
};

const waitForImage = async (image: HTMLImageElement) => {
	if (image.complete) {
		if (image.decode && image.naturalWidth > 0) {
			try {
				await image.decode();
			} catch {
				// Broken or cross-origin images should not block the whole export.
			}
		}
		return;
	}

	await Promise.race([
		new Promise<void>((resolve) => {
			const cleanup = () => {
				image.removeEventListener('load', cleanup);
				image.removeEventListener('error', cleanup);
				resolve();
			};

			image.addEventListener('load', cleanup, { once: true });
			image.addEventListener('error', cleanup, { once: true });
		}),
		new Promise<void>((resolve) => setTimeout(resolve, 2500))
	]);

	if (image.decode && image.complete && image.naturalWidth > 0) {
		try {
			await image.decode();
		} catch {
			// keep the rendered fallback if decode fails
		}
	}
};

const waitForImages = async (root: HTMLElement) => {
	const images = Array.from(root.querySelectorAll<HTMLImageElement>('img'));
	await Promise.all(images.map((image) => waitForImage(image)));
	await waitForNextFrame();
};

const parseCssColor = (value: string): RgbaColor | null => {
	if (!value || value === 'transparent' || value === 'initial' || value === 'inherit') {
		return null;
	}

	const rgbMatch = value.match(
		/rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:[,\s/]+([\d.]+))?\s*\)/i
	);

	if (rgbMatch) {
		return {
			r: Number(rgbMatch[1]),
			g: Number(rgbMatch[2]),
			b: Number(rgbMatch[3]),
			a: rgbMatch[4] === undefined ? 1 : Number(rgbMatch[4])
		};
	}

	const hexMatch = value.trim().match(/^#([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i);
	if (!hexMatch) {
		return null;
	}

	let hex = hexMatch[1];
	if (hex.length === 3) {
		hex = hex
			.split('')
			.map((char) => `${char}${char}`)
			.join('');
	}

	if (hex.length === 6) {
		return {
			r: parseInt(hex.slice(0, 2), 16),
			g: parseInt(hex.slice(2, 4), 16),
			b: parseInt(hex.slice(4, 6), 16),
			a: 1
		};
	}

	return {
		r: parseInt(hex.slice(0, 2), 16),
		g: parseInt(hex.slice(2, 4), 16),
		b: parseInt(hex.slice(4, 6), 16),
		a: parseInt(hex.slice(6, 8), 16) / 255
	};
};

const getLuminance = (color: RgbaColor | null) => {
	if (!color) {
		return 1;
	}

	const normalize = (channel: number) => {
		const value = channel / 255;
		return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
	};

	return (
		0.2126 * normalize(color.r) + 0.7152 * normalize(color.g) + 0.0722 * normalize(color.b)
	);
};

const isDarkSurface = (color: RgbaColor | null) =>
	Boolean(color && color.a > 0.15 && getLuminance(color) < 0.38);

const isLightText = (color: RgbaColor | null) =>
	Boolean(color && color.a > 0.1 && getLuminance(color) > 0.82);

const sanitizeFileName = (title?: string | null) => {
	const baseName = (title ?? '').trim() || 'chat';
	return `chat-${baseName.replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_')}.pdf`;
};

const stabilizeModelIconsForExport = (root: HTMLElement) => {
	const wrappers = Array.from(root.querySelectorAll<HTMLElement>('.model-icon'));

	for (const wrapper of wrappers) {
		const rect = wrapper.getBoundingClientRect();
		const width = Math.max(Math.round(rect.width), 1);
		const height = Math.max(Math.round(rect.height), 1);
		const wrapperStyle = window.getComputedStyle(wrapper);
		const img = wrapper.querySelector<HTMLImageElement>('img');

		wrapper.style.width = `${width}px`;
		wrapper.style.height = `${height}px`;
		wrapper.style.minWidth = `${width}px`;
		wrapper.style.minHeight = `${height}px`;
		wrapper.style.maxWidth = `${width}px`;
		wrapper.style.maxHeight = `${height}px`;
		wrapper.style.display = 'inline-flex';
		wrapper.style.alignItems = 'center';
		wrapper.style.justifyContent = 'center';
		wrapper.style.flex = 'none';
		wrapper.style.overflow = 'hidden';
		wrapper.style.borderRadius = wrapperStyle.borderRadius;
		wrapper.style.backgroundColor = wrapperStyle.backgroundColor;
		wrapper.style.boxShadow = wrapperStyle.boxShadow;

		if (!img) {
			continue;
		}

		const imgStyle = window.getComputedStyle(img);
		img.style.width = `${width}px`;
		img.style.height = `${height}px`;
		img.style.minWidth = `${width}px`;
		img.style.minHeight = `${height}px`;
		img.style.maxWidth = `${width}px`;
		img.style.maxHeight = `${height}px`;
		img.style.display = 'block';
		img.style.objectFit = imgStyle.objectFit;
		img.style.transform = imgStyle.transform === 'none' ? '' : imgStyle.transform;
		img.style.transformOrigin = imgStyle.transformOrigin;
		img.style.filter = imgStyle.filter === 'none' ? '' : imgStyle.filter;
		img.style.borderRadius = imgStyle.borderRadius;
		img.style.opacity = '1';
		img.style.transition = 'none';
	}
};

const applyCompactAppearance = (root: HTMLElement) => {
	root.style.background = '#ffffff';
	root.style.color = '#111827';

	const elements = [root, ...Array.from(root.querySelectorAll<HTMLElement>('*'))];
	for (const element of elements) {
		element.style.animation = 'none';
		element.style.transition = 'none';
		element.style.backdropFilter = 'none';
		element.style.filter = 'none';
		element.style.boxShadow = 'none';

		const computed = window.getComputedStyle(element);
		const backgroundColor = parseCssColor(computed.backgroundColor);
		const textColor = parseCssColor(computed.color);
		const borderColor = parseCssColor(computed.borderColor);
		const tagName = element.tagName.toLowerCase();
		const isCodeLike = ['pre', 'code', 'blockquote', 'table', 'thead', 'tbody', 'tr', 'td', 'th'].includes(
			tagName
		);

		if (isDarkSurface(backgroundColor)) {
			element.style.backgroundColor = isCodeLike ? '#f3f4f6' : '#ffffff';
		}

		if (isLightText(textColor)) {
			element.style.color = '#111827';
		}

		if (isDarkSurface(borderColor)) {
			element.style.borderColor = '#d1d5db';
		}
	}
};

const buildClone = (sourceElement: HTMLElement, mode: ChatPdfExportMode, width: number) => {
	const clone = sourceElement.cloneNode(true) as HTMLElement;
	clone.style.position = 'absolute';
	clone.style.left = '-20000px';
	clone.style.top = '0';
	clone.style.height = 'auto';
	clone.style.overflow = 'visible';
	clone.style.maxWidth = 'none';
	clone.style.width = `${width}px`;
	clone.style.pointerEvents = 'none';
	clone.style.opacity = '1';
	clone.style.zIndex = '-1';
	clone.setAttribute('data-pdf-capture-mode', mode);

	return clone;
};

const getPxPerCssPixel = (root: HTMLElement, canvasWidth: number) => {
	const rootWidth = Math.max(root.getBoundingClientRect().width, 1);
	return canvasWidth / rootWidth;
};

const getRangeInCanvasPixels = (
	rootRect: DOMRect,
	element: HTMLElement,
	pxPerCssPixel: number
) => {
	const rect = element.getBoundingClientRect();
	const left = Math.max(0, Math.round((rect.left - rootRect.left) * pxPerCssPixel));
	const right = Math.max(left, Math.round((rect.right - rootRect.left) * pxPerCssPixel));
	const width = right - left;
	const top = Math.max(0, Math.round((rect.top - rootRect.top) * pxPerCssPixel));
	const bottom = Math.max(top, Math.round((rect.bottom - rootRect.top) * pxPerCssPixel));
	const height = bottom - top;

	if (height <= 1 || width <= 1) {
		return null;
	}

	return { left, right, width, top, bottom, height };
};

const filterContentImages = (images: HTMLImageElement[]) =>
	images.filter((image) => {
		if (image.closest('.model-icon') || image.getAttribute('alt') === 'profile') {
			return false;
		}

		const rect = image.getBoundingClientRect();
		return rect.width > 32 && rect.height > 32;
	});

const getContentImages = (root: HTMLElement) =>
	filterContentImages(Array.from(root.querySelectorAll<HTMLImageElement>(CONTENT_IMAGE_SELECTOR)));

const getImagePaginationElement = (image: HTMLImageElement) => {
	const button = image.closest('button');
	if (button && button.closest('[id^="message-"]')) {
		return button as HTMLElement;
	}

	return image;
};

const normalizeContentImages = (root: HTMLElement) => {
	for (const image of getContentImages(root)) {
		image.loading = 'eager';
		image.style.objectFit = 'contain';

		const wrapper = getImagePaginationElement(image);
		wrapper.style.overflow = 'visible';
		if (wrapper.tagName.toLowerCase() === 'button') {
			wrapper.style.textAlign = 'center';
		}
	}
};

const constrainImageHeight = (image: HTMLImageElement, maxHeight: number) => {
	const currentHeight = image.getBoundingClientRect().height;
	const nextHeight = Math.max(48, Math.floor(maxHeight));

	if (nextHeight >= currentHeight - 1) {
		return false;
	}

	const previousLimit = Number(image.dataset.pdfExportMaxHeight ?? Number.POSITIVE_INFINITY);
	if (nextHeight >= previousLimit - 1) {
		return false;
	}

	image.dataset.pdfExportMaxHeight = `${nextHeight}`;
	image.style.height = 'auto';
	image.style.maxHeight = `${nextHeight}px`;
	image.style.maxWidth = '100%';
	image.style.width = 'auto';
	image.style.objectFit = 'contain';

	return true;
};

const fitOversizedImagesToPage = async (root: HTMLElement, pageCssHeight: number) => {
	for (let pass = 0; pass < 6; pass += 1) {
		let adjusted = false;

		for (const image of getContentImages(root)) {
			const wrapper = getImagePaginationElement(image);
			const wrapperHeight = wrapper.getBoundingClientRect().height;

			if (wrapperHeight <= pageCssHeight - PAGE_EDGE_PADDING_PX * 2) {
				continue;
			}

			const imageHeight = image.getBoundingClientRect().height;
			const wrapperExtraHeight = Math.max(0, wrapperHeight - imageHeight);
			const nextImageHeight = pageCssHeight - PAGE_EDGE_PADDING_PX * 2 - wrapperExtraHeight;
			adjusted = constrainImageHeight(image, nextImageHeight) || adjusted;
		}

		if (!adjusted) {
			return;
		}

		await waitForNextFrame();
	}
};

const collectBreakOffsets = (root: HTMLElement, selector: string, canvasWidth: number) => {
	const rootRect = root.getBoundingClientRect();
	const pxPerCssPixel = getPxPerCssPixel(root, canvasWidth);
	const rootPixelHeight = Math.round(Math.max(root.scrollHeight, rootRect.height) * pxPerCssPixel);
	const offsets = new Set<number>();

	for (const element of root.querySelectorAll<HTMLElement>(selector)) {
		const range = getRangeInCanvasPixels(rootRect, element, pxPerCssPixel);
		if (!range) {
			continue;
		}

		if (range.top > 0 && range.top < rootPixelHeight) {
			offsets.add(range.top);
		}

		if (selector === SECONDARY_BREAK_SELECTOR && range.bottom > 0 && range.bottom < rootPixelHeight) {
			offsets.add(range.bottom);
		}
	}

	return Array.from(offsets).sort((left, right) => left - right);
};

const collectMessageRanges = (root: HTMLElement, canvasWidth: number) => {
	const rootRect = root.getBoundingClientRect();
	const pxPerCssPixel = getPxPerCssPixel(root, canvasWidth);
	const ranges: BlockRange[] = [];

	for (const message of root.querySelectorAll<HTMLElement>('[id^="message-"]')) {
		const range = getRangeInCanvasPixels(rootRect, message, pxPerCssPixel);
		if (!range) {
			continue;
		}

		ranges.push({
			...range,
			hasContentImage: Boolean(message.querySelector(CONTENT_IMAGE_SELECTOR))
		});
	}

	return ranges.sort((left, right) => left.top - right.top);
};

const collectAtomicRanges = (root: HTMLElement, canvasWidth: number) => {
	const rootRect = root.getBoundingClientRect();
	const pxPerCssPixel = getPxPerCssPixel(root, canvasWidth);
	const ranges: AtomicRange[] = [];
	const seen = new Set<HTMLElement>();

	for (const image of getContentImages(root)) {
		const element = getImagePaginationElement(image);
		if (seen.has(element)) {
			continue;
		}

		const range = getRangeInCanvasPixels(rootRect, element, pxPerCssPixel);
		if (range) {
			seen.add(element);
			ranges.push({ ...range, image });
		}
	}

	for (const element of root.querySelectorAll<HTMLElement>(ATOMIC_BLOCK_SELECTOR)) {
		if (seen.has(element) || element.querySelector(CONTENT_IMAGE_SELECTOR)) {
			continue;
		}

		const range = getRangeInCanvasPixels(rootRect, element, pxPerCssPixel);
		if (range) {
			seen.add(element);
			ranges.push(range);
		}
	}

	for (const message of root.querySelectorAll<HTMLElement>('[id^="message-"]')) {
		const range = getRangeInCanvasPixels(rootRect, message, pxPerCssPixel);
		if (!range || range.height <= Math.round(48 * pxPerCssPixel)) {
			continue;
		}

		const keepLength = Math.min(
			Math.round(MESSAGE_FIRST_BLOCK_KEEP_PX * pxPerCssPixel),
			range.height - Math.round(8 * pxPerCssPixel)
		);

		if (keepLength <= Math.round(48 * pxPerCssPixel)) {
			continue;
		}

		ranges.push({
			top: range.top,
			bottom: range.top + keepLength,
			height: keepLength
		});
	}

	return ranges.sort((left, right) => left.top - right.top);
};

const collectMessageHeadRanges = (root: HTMLElement, canvasWidth: number) => {
	const rootRect = root.getBoundingClientRect();
	const pxPerCssPixel = getPxPerCssPixel(root, canvasWidth);
	const ranges: BlockRange[] = [];

	for (const message of root.querySelectorAll<HTMLElement>('[id^="message-"]')) {
		const messageRange = getRangeInCanvasPixels(rootRect, message, pxPerCssPixel);
		const contentRoot = message.querySelector<HTMLElement>('.markdown-prose');

		if (!messageRange || !contentRoot) {
			continue;
		}

		const contentRange = getRangeInCanvasPixels(rootRect, contentRoot, pxPerCssPixel);
		if (!contentRange) {
			continue;
		}

		const headBottom = Math.min(messageRange.bottom, contentRange.top + Math.round(12 * pxPerCssPixel));
		if (headBottom <= messageRange.top + Math.round(24 * pxPerCssPixel)) {
			continue;
		}

		ranges.push({
			top: messageRange.top,
			bottom: headBottom,
			height: headBottom - messageRange.top
		});
	}

	return ranges.sort((left, right) => left.top - right.top);
};

const collectKeepTogetherRanges = (
	root: HTMLElement,
	canvasWidth: number,
	maxHeight: number
) => {
	const rootRect = root.getBoundingClientRect();
	const pxPerCssPixel = getPxPerCssPixel(root, canvasWidth);
	const ranges: BlockRange[] = [];
	const seen = new Set<string>();

	for (const element of root.querySelectorAll<HTMLElement>(KEEP_TOGETHER_SELECTOR)) {
		const range = getRangeInCanvasPixels(rootRect, element, pxPerCssPixel);
		if (!range || range.height > maxHeight) {
			continue;
		}

		const key = `${range.top}:${range.bottom}`;
		if (seen.has(key)) {
			continue;
		}

		seen.add(key);
		ranges.push(range);
	}

	return ranges.sort((left, right) => left.top - right.top);
};

const adjustTextMessageBreak = (
	offsetY: number,
	breakOffset: number,
	pagePixelHeight: number,
	messageRanges: BlockRange[],
	pxPerCssPixel: number
) => {
	const messageRange = messageRanges.find(
		(range) => breakOffset > range.top + 8 && breakOffset < range.bottom - 3
	);

	if (!messageRange || messageRange.top <= offsetY + 8) {
		return breakOffset;
	}

	const isOrphaningMessageHead =
		breakOffset - messageRange.top <= MESSAGE_HEAD_KEEP_WITH_BODY_PX * pxPerCssPixel;
	const isSplittingSmallTextMessage =
		!messageRange.hasContentImage &&
		messageRange.height <= pagePixelHeight * SMALL_TEXT_MESSAGE_MAX_HEIGHT_RATIO;

	if (!isOrphaningMessageHead && !isSplittingSmallTextMessage) {
		return breakOffset;
	}

	return messageRange.top;
};

const getRootHeight = (root: HTMLElement) =>
	Math.round(Math.max(root.scrollHeight, root.getBoundingClientRect().height));

const findSplitImageRange = (breakOffset: number, atomicRanges: AtomicRange[]) =>
	atomicRanges.find(
		(range) => Boolean(range.image) && breakOffset > range.top && breakOffset < range.bottom
	);

const findBlockingImageRange = (breakOffset: number, atomicRanges: AtomicRange[]) =>
	atomicRanges.find(
		(range) =>
			Boolean(range.image) &&
			breakOffset >= Math.max(0, range.top - IMAGE_BREAK_SAFETY_PX) &&
			breakOffset < range.bottom
	);

const findContainingRange = (
	breakOffset: number,
	ranges: BlockRange[],
	previousBreak: number
) => {
	for (let index = ranges.length - 1; index >= 0; index -= 1) {
		const range = ranges[index];
		if (range.top <= previousBreak + 1) {
			continue;
		}

		if (breakOffset > range.top + 2 && breakOffset < range.bottom - 2) {
			return range;
		}
	}

	return null;
};

const adjustBreakAgainstProtectedBlocks = (
	previousBreak: number,
	breakOffset: number,
	messageHeadRanges: BlockRange[],
	atomicRanges: BlockRange[],
	keepTogetherRanges: BlockRange[]
) => {
	let nextBreak = breakOffset;

	for (let pass = 0; pass < 4; pass += 1) {
		const messageHeadRange = findContainingRange(nextBreak, messageHeadRanges, previousBreak);
		if (messageHeadRange) {
			nextBreak = messageHeadRange.top;
			continue;
		}

		const atomicRange = findContainingRange(nextBreak, atomicRanges, previousBreak);
		if (atomicRange) {
			nextBreak = atomicRange.top;
			continue;
		}

		const keepTogetherRange = findContainingRange(nextBreak, keepTogetherRanges, previousBreak);
		if (keepTogetherRange) {
			nextBreak = keepTogetherRange.top;
			continue;
		}

		break;
	}

	return nextBreak;
};

const tryShrinkImageToFitPage = (
	imageRange: AtomicRange,
	pageStart: number,
	pageHeight: number
) => {
	const image = imageRange.image;
	if (!image) {
		return false;
	}

	const wrapper = getImagePaginationElement(image);
	const wrapperHeight = wrapper.getBoundingClientRect().height;
	const imageHeight = image.getBoundingClientRect().height;
	const pageBottom = pageStart + pageHeight;
	const availableWrapperHeight = pageBottom - imageRange.top;
	const wrapperExtraHeight = Math.max(0, wrapperHeight - imageHeight);
	const nextImageHeight = availableWrapperHeight - wrapperExtraHeight;
	const shrinkRatio = nextImageHeight / Math.max(imageHeight, 1);

	if (
		nextImageHeight <= 48 ||
		nextImageHeight >= imageHeight - 1 ||
		shrinkRatio < MIN_NEAR_PAGE_IMAGE_SHRINK_RATIO
	) {
		return false;
	}

	return constrainImageHeight(image, nextImageHeight);
};

const resolveImageBreak = (
	pageStart: number,
	pageHeight: number,
	breakOffset: number,
	atomicRanges: AtomicRange[]
) => {
	const splitImageRange = findSplitImageRange(breakOffset, atomicRanges);
	if (!splitImageRange) {
		return { breakOffset, layoutAdjusted: false };
	}

	if (tryShrinkImageToFitPage(splitImageRange, pageStart, pageHeight)) {
		return { breakOffset, layoutAdjusted: true };
	}

	if (splitImageRange.top > pageStart + 1) {
		return {
			breakOffset: Math.max(pageStart + 1, splitImageRange.top - IMAGE_BREAK_SAFETY_PX),
			layoutAdjusted: false
		};
	}

	return { breakOffset, layoutAdjusted: false };
};

const buildPageSlicesFromBreakOffsets = (breakOffsets: number[], totalHeight: number) => {
	const normalizedBreakOffsets =
		breakOffsets.length > 0 ? [...breakOffsets] : [Math.max(totalHeight, 1)];
	const slices: PageSlice[] = [];
	let offsetY = 0;

	for (let index = 0; index < normalizedBreakOffsets.length; index += 1) {
		const isLastBreak = index === normalizedBreakOffsets.length - 1;
		const rawBreak = isLastBreak ? totalHeight : normalizedBreakOffsets[index];
		const nextBreak = Math.max(offsetY + 1, Math.min(totalHeight, rawBreak));

		slices.push({
			offsetY,
			sliceHeight: nextBreak - offsetY
		});

		offsetY = nextBreak;
	}

	if (offsetY < totalHeight) {
		slices.push({
			offsetY,
			sliceHeight: totalHeight - offsetY
		});
	}

	return slices;
};

const normalizeBreakOffsets = (
	breakOffsets: number[],
	imageRanges: AtomicRange[],
	protectedAtomicRanges: BlockRange[],
	messageHeadRanges: BlockRange[],
	keepTogetherRanges: BlockRange[],
	totalHeight: number,
	maxSliceHeight: number
) => {
	const normalizedBreakOffsets =
		breakOffsets.length > 0 ? [...breakOffsets] : [Math.max(totalHeight, 1)];
	normalizedBreakOffsets[normalizedBreakOffsets.length - 1] = totalHeight;

	for (let pass = 0; pass < MAX_BREAK_NORMALIZATION_PASSES; pass += 1) {
		let changed = false;
		let previousBreak = 0;

		for (let index = 0; index < normalizedBreakOffsets.length; index += 1) {
			const isLastBreak = index === normalizedBreakOffsets.length - 1;
			const remainingBreaks = normalizedBreakOffsets.length - index - 1;
			let nextBreak = isLastBreak ? totalHeight : normalizedBreakOffsets[index];
			const maxBreak = isLastBreak ? totalHeight : totalHeight - remainingBreaks;
			const maxAllowedBreak = isLastBreak
				? totalHeight
				: Math.min(maxBreak, previousBreak + maxSliceHeight);
			nextBreak = Math.max(previousBreak + 1, Math.min(maxAllowedBreak, nextBreak));

			const blockingImageRange = findBlockingImageRange(nextBreak, imageRanges);

			if (blockingImageRange && blockingImageRange.top > previousBreak + 1) {
				nextBreak = Math.max(previousBreak + 1, blockingImageRange.top - IMAGE_BREAK_SAFETY_PX);
			}

			nextBreak = adjustBreakAgainstProtectedBlocks(
				previousBreak,
				nextBreak,
				messageHeadRanges,
				protectedAtomicRanges,
				keepTogetherRanges
			);

			const clampedBreak = Math.max(previousBreak + 1, Math.min(maxAllowedBreak, nextBreak));

			if (normalizedBreakOffsets[index] !== clampedBreak) {
				normalizedBreakOffsets[index] = clampedBreak;
				changed = true;
			}

			previousBreak = normalizedBreakOffsets[index];
		}

		normalizedBreakOffsets[normalizedBreakOffsets.length - 1] = totalHeight;

		if (!changed) {
			break;
		}
	}

	return normalizedBreakOffsets;
};

const getBandInkScore = (
	imageData: Uint8ClampedArray,
	canvasWidth: number,
	canvasHeight: number,
	startRow: number,
	bandHeight: number,
	background: RgbaColor
) => {
	let inkPixels = 0;
	let sampledPixels = 0;

	for (let row = startRow; row < Math.min(startRow + bandHeight, canvasHeight); row += 1) {
		for (let col = 0; col < canvasWidth; col += 2) {
			const index = (row * canvasWidth + col) * 4;
			const r = imageData[index];
			const g = imageData[index + 1];
			const b = imageData[index + 2];
			const a = imageData[index + 3];

			if (a < 12) {
				sampledPixels += 1;
				continue;
			}

			const distance =
				Math.abs(r - background.r) +
				Math.abs(g - background.g) +
				Math.abs(b - background.b);

			if (distance > 48) {
				inkPixels += 1;
			}

			sampledPixels += 1;
		}
	}

	return sampledPixels === 0 ? 1 : inkPixels / sampledPixels;
};

const findWhitespaceBreak = (
	canvas: HTMLCanvasElement,
	currentTop: number,
	targetBottom: number,
	minPageFill: number,
	background: RgbaColor
) => {
	const ctx = canvas.getContext('2d');
	if (!ctx) {
		return null;
	}

	const searchHeight = Math.min(220, Math.max(Math.floor((targetBottom - currentTop) * 0.22), 80));
	const searchStart = Math.max(currentTop + minPageFill, targetBottom - searchHeight);
	const searchEnd = Math.max(searchStart, targetBottom - 12);

	if (searchEnd - searchStart < 8) {
		return null;
	}

	const imageData = ctx.getImageData(0, searchStart, canvas.width, searchEnd - searchStart + 1).data;
	let bestRow = -1;
	let bestScore = Number.POSITIVE_INFINITY;

	for (let row = 0; row <= searchEnd - searchStart; row += 2) {
		const absoluteRow = searchStart + row;
		const score = getBandInkScore(
			imageData,
			canvas.width,
			searchEnd - searchStart + 1,
			row,
			4,
			background
		);

		if (score < bestScore) {
			bestScore = score;
			bestRow = absoluteRow;
		}
	}

	return bestScore < 0.035 ? bestRow : null;
};

const snapBreakOffsetsToWhitespace = (
	canvas: HTMLCanvasElement,
	breakOffsets: number[],
	pagePixelHeight: number,
	background: RgbaColor
) => {
	const snappedBreakOffsets = [...breakOffsets];
	const minPageFill = Math.floor(pagePixelHeight * MIN_PAGE_FILL_RATIO);
	let previousBreak = 0;

	for (let index = 0; index < snappedBreakOffsets.length - 1; index += 1) {
		const currentBreak = snappedBreakOffsets[index];
		const whitespaceBreak = findWhitespaceBreak(
			canvas,
			previousBreak,
			currentBreak,
			minPageFill,
			background
		);

		if (whitespaceBreak && whitespaceBreak > previousBreak + 1 && whitespaceBreak <= currentBreak) {
			snappedBreakOffsets[index] = whitespaceBreak;
		}

		previousBreak = snappedBreakOffsets[index];
	}

	return snappedBreakOffsets;
};

const findActualCanvasImageRange = (
	canvas: HTMLCanvasElement,
	approximateRange: AtomicRange,
	background: RgbaColor
) => {
	const ctx = canvas.getContext('2d');
	if (!ctx || approximateRange.left === undefined || approximateRange.right === undefined) {
		return approximateRange;
	}

	const scanLeft = Math.max(0, approximateRange.left - 8);
	const scanRight = Math.min(canvas.width, approximateRange.right + 8);
	const scanTop = Math.max(0, approximateRange.top - CANVAS_IMAGE_POSITION_SEARCH_PX);
	const scanBottom = Math.min(canvas.height, approximateRange.bottom + CANVAS_IMAGE_POSITION_SEARCH_PX);
	const scanWidth = scanRight - scanLeft;
	const scanHeight = scanBottom - scanTop;

	if (scanWidth <= 1 || scanHeight <= 1) {
		return approximateRange;
	}

	const rowThreshold = Math.max(
		CANVAS_IMAGE_MIN_ROW_PIXELS,
		Math.floor(scanWidth * CANVAS_IMAGE_ROW_THRESHOLD_RATIO)
	);
	const edgeRowThreshold = Math.max(
		CANVAS_IMAGE_MIN_EDGE_ROW_PIXELS,
		Math.floor(scanWidth * CANVAS_IMAGE_EDGE_ROW_THRESHOLD_RATIO)
	);
	const imageData = ctx.getImageData(scanLeft, scanTop, scanWidth, scanHeight).data;
	const rowForegroundCounts = new Array<number>(scanHeight).fill(0);
	let actualTop = -1;
	let actualBottom = -1;

	for (let row = 0; row < scanHeight; row += 1) {
		let foregroundPixels = 0;

		for (let col = 0; col < scanWidth; col += 2) {
			const index = (row * scanWidth + col) * 4;
			const alpha = imageData[index + 3];
			if (alpha < 24) {
				continue;
			}

			const distance =
				Math.abs(imageData[index] - background.r) +
				Math.abs(imageData[index + 1] - background.g) +
				Math.abs(imageData[index + 2] - background.b);

			if (distance > 72) {
				foregroundPixels += 1;
			}
		}

		rowForegroundCounts[row] = foregroundPixels;

		if (foregroundPixels >= rowThreshold) {
			const absoluteRow = scanTop + row;
			if (actualTop === -1) {
				actualTop = absoluteRow;
			}
			actualBottom = absoluteRow + 1;
		}
	}

	if (actualTop === -1 || actualBottom <= actualTop) {
		return approximateRange;
	}

	let expandedTop = actualTop;
	let blankGap = 0;
	for (let row = actualTop - scanTop - 1; row >= 0; row -= 1) {
		if (rowForegroundCounts[row] >= edgeRowThreshold) {
			expandedTop = scanTop + row;
			blankGap = 0;
			continue;
		}

		blankGap += 1;
		if (blankGap > CANVAS_IMAGE_EDGE_GAP_PX) {
			break;
		}
	}

	let expandedBottom = actualBottom;
	blankGap = 0;
	for (let row = actualBottom - scanTop; row < scanHeight; row += 1) {
		if (rowForegroundCounts[row] >= edgeRowThreshold) {
			expandedBottom = scanTop + row + 1;
			blankGap = 0;
			continue;
		}

		blankGap += 1;
		if (blankGap > CANVAS_IMAGE_EDGE_GAP_PX) {
			break;
		}
	}

	const protectedTop = Math.max(0, Math.min(approximateRange.top, expandedTop));
	const protectedBottom = Math.min(
		canvas.height,
		Math.max(approximateRange.bottom, expandedBottom)
	);

	return {
		...approximateRange,
		top: protectedTop,
		bottom: protectedBottom,
		height: protectedBottom - protectedTop
	};
};

const resolveCanvasImageRanges = (
	canvas: HTMLCanvasElement,
	approximateImageRanges: AtomicRange[],
	background: RgbaColor
) =>
	approximateImageRanges.map((range) => findActualCanvasImageRange(canvas, range, background));

const planPageSlices = async (root: HTMLElement, pageCssHeight: number) => {
	for (let pass = 0; pass < MAX_PAGE_SLICE_PLAN_PASSES; pass += 1) {
		const layoutWidth = Math.max(root.getBoundingClientRect().width, 1);
		const primaryBreakOffsets = collectBreakOffsets(root, PRIMARY_BREAK_SELECTOR, layoutWidth);
		const secondaryBreakOffsets = collectBreakOffsets(root, SECONDARY_BREAK_SELECTOR, layoutWidth);
		const messageRanges = collectMessageRanges(root, layoutWidth);
		const atomicRanges = collectAtomicRanges(root, layoutWidth);
		const protectedAtomicRanges = atomicRanges.filter(
			(range) => !range.image && range.height < pageCssHeight * ATOMIC_BLOCK_MAX_HEIGHT_RATIO
		);
		const messageHeadRanges = collectMessageHeadRanges(root, layoutWidth);
		const keepTogetherRanges = collectKeepTogetherRanges(
			root,
			layoutWidth,
			Math.floor(pageCssHeight * KEEP_TOGETHER_MAX_HEIGHT_RATIO)
		);
		const totalHeight = getRootHeight(root);
		const slices: PageSlice[] = [];
		const minPageFill = Math.floor(pageCssHeight * MIN_PAGE_FILL_RATIO);
		const minSliceHeight = Math.floor(pageCssHeight * MIN_SLICE_HEIGHT_RATIO);
		const preferredMessageFill = Math.floor(pageCssHeight * PREFERRED_MESSAGE_FILL_RATIO);
		const crossingThreshold = Math.min(
			MESSAGE_CROSSING_THRESHOLD_CSS_PX,
			Math.floor(pageCssHeight * 0.22)
		);
		let offsetY = 0;
		let layoutAdjusted = false;

		while (offsetY < totalHeight) {
			if (offsetY + pageCssHeight >= totalHeight) {
				slices.push({
					offsetY,
					sliceHeight: totalHeight - offsetY
				});
				break;
			}

			const targetBottom = offsetY + pageCssHeight;
			const crossingMessage = messageRanges.find(
				(range) =>
					range.top > offsetY + minSliceHeight &&
					range.top < targetBottom &&
					range.bottom > targetBottom &&
					targetBottom - range.top < crossingThreshold
			);
			const crossingAtomic = atomicRanges.find(
				(range) =>
					range.top > offsetY &&
					range.top < targetBottom &&
					range.bottom > targetBottom &&
					(Boolean(range.image) || range.height < pageCssHeight * ATOMIC_BLOCK_MAX_HEIGHT_RATIO)
			);
			const primaryBreak = [...primaryBreakOffsets]
				.reverse()
				.find((offset) => offset >= offsetY + preferredMessageFill && offset <= targetBottom - 12);
			const secondaryBreak = [...secondaryBreakOffsets]
				.reverse()
				.find((offset) => offset >= offsetY + minPageFill && offset <= targetBottom - 12);
			const candidateBreak = adjustBreakAgainstProtectedBlocks(
				offsetY,
				adjustTextMessageBreak(
					offsetY,
					crossingAtomic?.top ??
						crossingMessage?.top ??
						primaryBreak ??
						secondaryBreak ??
						targetBottom,
					pageCssHeight,
					messageRanges,
					1
				),
				messageHeadRanges,
				protectedAtomicRanges,
				keepTogetherRanges
			);
			const resolvedBreak = resolveImageBreak(
				offsetY,
				pageCssHeight,
				candidateBreak,
				atomicRanges
			);

			if (resolvedBreak.layoutAdjusted) {
				layoutAdjusted = true;
				break;
			}

			const nextBreak = adjustBreakAgainstProtectedBlocks(
				offsetY,
				resolvedBreak.breakOffset > offsetY ? resolvedBreak.breakOffset : targetBottom,
				messageHeadRanges,
				protectedAtomicRanges,
				keepTogetherRanges
			);
			slices.push({
				offsetY,
				sliceHeight: Math.max(nextBreak - offsetY, 1)
			});

			offsetY = nextBreak;
		}

		if (!layoutAdjusted) {
			const imageRanges = atomicRanges.filter((range) => Boolean(range.image));
			const cssBreakOffsets = slices.map((slice) => slice.offsetY + slice.sliceHeight);
			const safeBreakOffsets = normalizeBreakOffsets(
				cssBreakOffsets,
				imageRanges,
				protectedAtomicRanges,
				messageHeadRanges,
				keepTogetherRanges,
				totalHeight,
				pageCssHeight
			);

			return buildPageSlicesFromBreakOffsets(safeBreakOffsets, totalHeight);
		}

		await waitForNextFrame();
		await waitForNextFrame();
	}

	return [
		{
			offsetY: 0,
			sliceHeight: getRootHeight(root)
		}
	];
};

const mapPageSlicesToCanvas = (
	cssPageSlices: PageSlice[],
	root: HTMLElement,
	canvas: HTMLCanvasElement,
	background: RgbaColor,
	pageCssHeight: number
) => {
	const pxPerCssPixel = getPxPerCssPixel(root, canvas.width);
	const pagePixelHeight = Math.floor(pageCssHeight * pxPerCssPixel);
	const canvasBreakOffsets = cssPageSlices.map((slice, index) => {
		const cssBreakOffset = slice.offsetY + slice.sliceHeight;
		if (index === cssPageSlices.length - 1) {
			return canvas.height;
		}

		return Math.max(1, Math.min(canvas.height, Math.floor(cssBreakOffset * pxPerCssPixel)));
	});
	const atomicRanges = collectAtomicRanges(root, canvas.width);
	const imageRanges = resolveCanvasImageRanges(
		canvas,
		atomicRanges.filter((range) => Boolean(range.image)),
		background
	);
	const safeBreakOffsets = normalizeBreakOffsets(
		canvasBreakOffsets,
		imageRanges,
		atomicRanges.filter(
			(range) => !range.image && range.height < pagePixelHeight * ATOMIC_BLOCK_MAX_HEIGHT_RATIO
		),
		collectMessageHeadRanges(root, canvas.width),
		collectKeepTogetherRanges(
			root,
			canvas.width,
			Math.floor(pagePixelHeight * KEEP_TOGETHER_MAX_HEIGHT_RATIO)
		),
		canvas.height,
		pagePixelHeight
	);
	const whitespaceSafeBreakOffsets = snapBreakOffsetsToWhitespace(
		canvas,
		safeBreakOffsets,
		pagePixelHeight,
		background
	);
	const finalBreakOffsets = normalizeBreakOffsets(
		whitespaceSafeBreakOffsets,
		imageRanges,
		atomicRanges.filter(
			(range) => !range.image && range.height < pagePixelHeight * ATOMIC_BLOCK_MAX_HEIGHT_RATIO
		),
		collectMessageHeadRanges(root, canvas.width),
		collectKeepTogetherRanges(
			root,
			canvas.width,
			Math.floor(pagePixelHeight * KEEP_TOGETHER_MAX_HEIGHT_RATIO)
		),
		canvas.height,
		pagePixelHeight
	);

	return buildPageSlicesFromBreakOffsets(finalBreakOffsets, canvas.height);
};

const buildPageSlices = (
	root: HTMLElement,
	canvas: HTMLCanvasElement,
	cssPageSlices: PageSlice[],
	background: RgbaColor,
	pageCssHeight: number
) => mapPageSlicesToCanvas(cssPageSlices, root, canvas, background, pageCssHeight);

const saveCanvasAsPdf = async (
	canvas: HTMLCanvasElement,
	title: string | null | undefined,
	quality: number,
	darkMode: boolean,
	pageSlices: PageSlice[]
) => {
	const jspdfModule = await import('jspdf');
	const JsPdf = (jspdfModule as any).jsPDF ?? (jspdfModule as any).default;
	const pdf = new JsPdf('p', 'mm', 'a4');
	let page = 0;

	for (const { offsetY, sliceHeight } of pageSlices) {
		const pageCanvas = document.createElement('canvas');
		pageCanvas.width = canvas.width;
		pageCanvas.height = sliceHeight;

		const ctx = pageCanvas.getContext('2d');
		if (!ctx) {
			throw new Error('无法创建 PDF 画布。');
		}

		ctx.drawImage(canvas, 0, offsetY, canvas.width, sliceHeight, 0, 0, canvas.width, sliceHeight);

		const imageData = pageCanvas.toDataURL('image/jpeg', quality);
		const availableWidthMM = PDF_PAGE_WIDTH_MM;
		const availableHeightMM = Math.max(
			1,
			PDF_PAGE_HEIGHT_MM - PDF_PAGE_TOP_MARGIN_MM - PDF_PAGE_BOTTOM_MARGIN_MM
		);
		const widthScale = availableWidthMM / Math.max(pageCanvas.width, 1);
		const heightScale = availableHeightMM / Math.max(pageCanvas.height, 1);
		const scale = Math.min(widthScale, heightScale);
		const imageWidthMM = pageCanvas.width * scale;
		const imageHeightMM = pageCanvas.height * scale;
		const imageXMM = (PDF_PAGE_WIDTH_MM - imageWidthMM) / 2;
		const imageYMM = PDF_PAGE_TOP_MARGIN_MM;

		if (page > 0) {
			pdf.addPage();
		}

		if (darkMode) {
			pdf.setFillColor(2, 6, 23);
			pdf.rect(0, 0, PDF_PAGE_WIDTH_MM, PDF_PAGE_HEIGHT_MM, 'F');
		} else {
			pdf.setFillColor(255, 255, 255);
			pdf.rect(0, 0, PDF_PAGE_WIDTH_MM, PDF_PAGE_HEIGHT_MM, 'F');
		}

		pdf.addImage(imageData, 'JPEG', imageXMM, imageYMM, imageWidthMM, imageHeightMM);
		pageCanvas.width = 0;
		pageCanvas.height = 0;
		page += 1;
	}

	pdf.save(sanitizeFileName(title));
};

export const exportChatPdfFromElement = async ({
	sourceElement,
	title,
	mode = 'stylized',
	darkMode = false
}: ExportChatPdfOptions) => {
	const { default: html2canvas } = await import('html2canvas-pro');
	const config = MODE_CONFIG[mode];
	const clone = buildClone(sourceElement, mode, config.width);

	document.body.appendChild(clone);

	try {
		await waitForStableLayout();
		await waitForImages(clone);
		stabilizeModelIconsForExport(clone);
		normalizeContentImages(clone);

		if (mode === 'compact') {
			applyCompactAppearance(clone);
			await waitForNextFrame();
		}

		const pageCssHeight = Math.floor((config.width / PDF_PAGE_WIDTH_MM) * PDF_PAGE_HEIGHT_MM);
		await fitOversizedImagesToPage(clone, pageCssHeight);
		const cssPageSlices = await planPageSlices(clone, pageCssHeight);
		await waitForStableLayout();
		await waitForImages(clone);

		const backgroundColor = config.backgroundColor(darkMode);
			const canvas = await html2canvas(clone, {
				backgroundColor,
				useCORS: true,
				scale: config.scale,
				width: config.width,
				windowWidth: config.width,
				logging: false
			});
			const background =
				parseCssColor(backgroundColor) ??
				({
					r: 255,
					g: 255,
					b: 255,
					a: 1
				} satisfies RgbaColor);
			const pageSlices = buildPageSlices(clone, canvas, cssPageSlices, background, pageCssHeight);

			await saveCanvasAsPdf(
				canvas,
			title,
			config.quality,
			darkMode && mode === 'stylized',
			pageSlices
		);
	} finally {
		clone.remove();
	}
};
