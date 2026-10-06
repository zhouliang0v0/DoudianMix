import js from '@eslint/js';
import stylistic from '@stylistic/eslint-plugin';
import globals from 'globals';

// Stylistic indent deliberately ignores the first token after assignments.
// Enforce Google's +4 minimum for those expression continuations separately.
const continuationIndent = {
  meta: {
    type: 'layout',
    fixable: 'whitespace',
    schema: [],
    messages: {
      minimum: 'Indent continuation at least 4 spaces beyond its statement.',
    },
  },
  create(context) {
    const source = context.sourceCode;
    function check(node, expression) {
      if (
        !expression ||
        ['BlockStatement', 'ObjectExpression', 'ArrayExpression'].includes(
          expression.type,
        )
      ) {
        return;
      }
      if (expression.loc.start.line <= node.loc.start.line) {
        return;
      }
      let statement = node;
      while (
        statement.parent &&
        !statement.type.endsWith('Statement') &&
        !statement.type.endsWith('Declaration') &&
        statement.type !== 'Property'
      ) {
        statement = statement.parent;
      }
      if (expression.loc.start.line <= statement.loc.start.line) {
        return;
      }
      const baseLine =
        node.type === 'ArrowFunctionExpression'
          ? node.loc.start.line
          : statement.loc.start.line;
      const base = source.lines[baseLine - 1].match(/^ */)[0].length;
      const line = source.lines[expression.loc.start.line - 1];
      const actual = line.match(/^ */)[0].length;
      if (actual < base + 4) {
        const start = source.getIndexFromLoc({
          line: expression.loc.start.line,
          column: 0,
        });
        context.report({
          node: expression,
          messageId: 'minimum',
          fix: (fixer) =>
            fixer.replaceTextRange(
              [start, start + actual],
              ' '.repeat(base + 4),
            ),
        });
      }
    }
    return {
      AssignmentExpression: (node) => check(node, node.right),
      VariableDeclarator: (node) => check(node, node.init),
      Property: (node) => check(node, node.value),
      'BinaryExpression, LogicalExpression': (node) => check(node, node.right),
      ConditionalExpression: (node) => {
        check(node, node.test);
        check(node, node.consequent);
        check(node, node.alternate);
      },
      ArrowFunctionExpression: (node) => check(node, node.body),
    };
  },
};

export default [
  {
    files: ['public/*.js'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: globals.browser,
    },
    plugins: {
      '@stylistic': stylistic,
      google: {rules: {'continuation-indent': continuationIndent}},
    },
    rules: {
      ...js.configs.recommended.rules,
      curly: ['error', 'all'],
      eqeqeq: ['error', 'always'],
      'no-var': 'error',
      'prefer-const': 'error',
      'prefer-arrow-callback': 'error',
      'google/continuation-indent': 'error',
      '@stylistic/indent': [
        'error',
        2,
        {
          SwitchCase: 1,
          CallExpression: {arguments: 2},
          FunctionDeclaration: {parameters: 2},
          FunctionExpression: {parameters: 2},
          MemberExpression: 2,
          // Only leaf property values delegate their wrapped-token minimum.
          // Property keys, block bodies and literal contents retain the +2 check.
          ignoredNodes: [
            'ConditionalExpression',
            'Property > Literal.value',
            'Property > Identifier.value',
            'ArrowFunctionExpression[body.type!="BlockStatement"][body.type!="ObjectExpression"][body.type!="ArrayExpression"] > :expression.body',
          ],
        },
      ],
      '@stylistic/quotes': ['error', 'single', {avoidEscape: true}],
      '@stylistic/semi': ['error', 'always'],
      '@stylistic/brace-style': ['error', '1tbs'],
      '@stylistic/array-bracket-spacing': ['error', 'never'],
      '@stylistic/object-curly-spacing': ['error', 'never'],
      '@stylistic/comma-spacing': 'error',
      '@stylistic/comma-dangle': ['error', 'always-multiline'],
      '@stylistic/key-spacing': 'error',
      '@stylistic/keyword-spacing': 'error',
      '@stylistic/space-infix-ops': 'error',
      '@stylistic/arrow-parens': ['error', 'always'],
      '@stylistic/arrow-spacing': 'error',
      '@stylistic/space-before-blocks': 'error',
      '@stylistic/no-trailing-spaces': 'error',
      '@stylistic/eol-last': ['error', 'always'],
      '@stylistic/max-statements-per-line': ['error', {max: 1}],
      '@stylistic/max-len': [
        'error',
        {
          code: 80,
          ignoreStrings: true,
          ignoreTemplateLiterals: true,
          ignoreUrls: true,
          ignoreRegExpLiterals: true,
        },
      ],
    },
  },
];
