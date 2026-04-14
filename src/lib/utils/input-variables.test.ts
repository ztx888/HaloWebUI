import { describe, expect, it } from 'vitest';

import {
	extractInputVariables,
	parseJsonValue,
	parseVariableDefinition,
	splitProperties
} from './index';

describe('input variable parsing', () => {
	it('splits nested property definitions without breaking on JSON delimiters', () => {
		expect(splitProperties('select:options=["a","b"]:required', ':')).toEqual([
			'select',
			'options=["a","b"]',
			'required'
		]);
	});

	it('parses typed variable definitions with flags and json values', () => {
		expect(
			parseVariableDefinition(
				'select:options=["alpha","beta"]:placeholder="Pick one":required:default="beta"'
			)
		).toEqual({
			type: 'select',
			options: ['alpha', 'beta'],
			placeholder: 'Pick one',
			required: true,
			default: 'beta'
		});
	});

	it('parses primitive json-like values', () => {
		expect(parseJsonValue('true')).toBe(true);
		expect(parseJsonValue('42')).toBe(42);
		expect(parseJsonValue('"hello"')).toBe('hello');
	});

	it('extracts both plain and typed template variables', () => {
		expect(
			extractInputVariables(
				'Hello {{name}} {{tone|select:options=["warm","direct"]:default="warm"}} {{count|number:min=1:max=5}}'
			)
		).toEqual({
			name: { type: 'text' },
			tone: {
				type: 'select',
				options: ['warm', 'direct'],
				default: 'warm'
			},
			count: {
				type: 'number',
				min: 1,
				max: 5
			}
		});
	});
});
