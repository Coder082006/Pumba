import js from '@eslint/js';
import tseslint from 'typescript-eslint';

/**
 * Shared flat ESLint config.
 *
 * The money rule is the one worth reading: SRS §9.1 sends amounts as decimal
 * strings because JSON numbers are IEEE 754 doubles. `parseFloat` on a price
 * is a defect, so it is banned outright rather than left to review.
 */
export default tseslint.config(
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    rules: {
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      '@typescript-eslint/consistent-type-imports': [
        'error',
        { prefer: 'type-imports', fixStyle: 'inline-type-imports' },
      ],
      'no-restricted-globals': [
        'error',
        {
          name: 'parseFloat',
          message:
            'Money crosses the wire as a decimal string (SRS §9.1). parseFloat would ' +
            'silently lose precision. Use a decimal library.',
        },
      ],
      eqeqeq: ['error', 'always', { null: 'ignore' }],
      'no-console': ['warn', { allow: ['warn', 'error'] }],
    },
  },
  {
    ignores: ['dist/**', '.next/**', 'node_modules/**', '**/*.d.ts'],
  },
);
