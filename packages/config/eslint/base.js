import js from '@eslint/js';
import reactHooks from 'eslint-plugin-react-hooks';
import tseslint from 'typescript-eslint';

/**
 * Shared flat ESLint config.
 *
 * The money rule is the one worth reading: SRS §9.1 sends amounts as decimal
 * strings because JSON numbers are IEEE 754 doubles. `parseFloat` on a price
 * is a defect, so it is banned outright rather than left to review.
 *
 * `react-hooks` was configured **nowhere** until now, in a workspace whose
 * two applications are both React. That is not a hypothetical gap: the map's
 * effect took `pins` and `center` as dependencies, both object literals
 * recreated on every render, so it tore down and rebuilt MapLibre on every
 * parent update. `exhaustive-deps` is the rule that names exactly that, and
 * it was not running. The bug was found by watching a map flicker.
 */
export default tseslint.config(
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    plugins: { 'react-hooks': reactHooks },
    rules: {
      // Errors, not warnings. A warning in a workspace that already lints
      // clean is a line nobody reads, and both of these describe real
      // defects rather than style.
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'error',
    },
  },
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
