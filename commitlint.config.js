module.exports = {
  extends: ['@commitlint/config-conventional'],
  rules: {
    'type-enum': [2, 'always', [
      'feat', 'fix', 'docs', 'style', 'refactor',
      'perf', 'test', 'chore', 'ci', 'revert'
    ]],
    'type-case': [2, 'always', 'lower-case'],
    'type-empty': [2, 'never'],
    'subject-empty': [2, 'never'],
    // 关闭英文大小写检查——中文 subject 不需要
    'subject-case': [0],
    // 放宽 header 长度（中文占宽大）
    'header-max-length': [2, 'always', 120],
    // scope 也关掉 case 检查
    'scope-case': [0],
  },
  prompt: {
    messages: {
      type: '选择提交类型:',
      scope: '输入影响范围（可选，中文）:',
      subject: '填写简短描述（中文动宾短语）:',
      body: '填写详细描述（可选，用 | 换行）:',
      breaking: '列出不兼容变更（可选）:',
      footer: '关联的 Issue（可选）:',
      confirmCommit: '确认提交？',
    },
  },
};
