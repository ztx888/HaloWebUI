import tippy from 'tippy.js';

export function getSuggestionRenderer(Component: any, ComponentProps = {}) {
	return function suggestionRenderer() {
		let component = null;
		let container: HTMLDivElement | null = null;
		let popup: any = null;
		let refEl: HTMLDivElement | null = null;

		return {
			onStart: (props: any) => {
				container = document.createElement('div');
				container.className = 'suggestion-list-container';
				document.body.appendChild(container);

				component = new Component({
					target: container,
					props: {
						char: props?.text?.charAt(0),
						query: props?.query,
						command: (item) => {
							props.command({ id: item.id, label: item.label });
						},
						...ComponentProps
					},
					context: new Map<string, any>([['i18n', ComponentProps?.i18n]])
				});

				refEl = document.createElement('div');
				Object.assign(refEl.style, {
					position: 'fixed',
					left: '0px',
					top: '0px',
					width: '0px',
					height: '0px'
				});
				document.body.appendChild(refEl);

				popup = tippy(refEl, {
					getReferenceClientRect: props.clientRect,
					appendTo: () => document.body,
					content: container,
					interactive: true,
					trigger: 'manual',
					theme: 'transparent',
					placement: 'top-start',
					offset: [-10, -2],
					arrow: false,
					popperOptions: {
						strategy: 'fixed',
						modifiers: [
							{
								name: 'preventOverflow',
								options: {
									boundary: 'viewport',
									altAxis: true,
									tether: true,
									padding: 8
								}
							},
							{
								name: 'flip',
								options: {
									boundary: 'viewport',
									fallbackPlacements: ['top-end', 'bottom-start', 'bottom-end']
								}
							},
							{ name: 'computeStyles', options: { adaptive: true } }
						]
					},
					interactiveBorder: 8
				});

				popup?.show();
			},

			onUpdate: (props: any) => {
				if (!component) {
					return;
				}

				component.$set({
					query: props.query,
					command: (item) => {
						props.command({ id: item.id, label: item.label });
					}
				});

				if (props.clientRect && popup) {
					popup.setProps({ getReferenceClientRect: props.clientRect });
				}
			},

			onKeyDown: (props: any) => {
				return component?._onKeyDown?.(props.event) ?? false;
			},

			onExit: () => {
				popup?.destroy();
				popup = null;

				try {
					component?.$destroy();
				} catch (error) {
					console.error('Error unmounting suggestion component:', error);
				}

				component = null;

				if (container?.parentNode) {
					container.parentNode.removeChild(container);
				}
				container = null;

				if (refEl?.parentNode) {
					refEl.parentNode.removeChild(refEl);
				}
				refEl = null;
			}
		};
	};
}
