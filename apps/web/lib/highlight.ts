import type { CodeLanguage } from '@/lib/types'

export type TokenType = 'keyword' | 'string' | 'comment' | 'number' | 'function' | 'property' | 'punctuation' | 'plain'

export interface Token {
  type: TokenType
  value: string
}

const JS_KEYWORDS = new Set(
  'abstract as async await break case catch class const continue debugger default delete do else enum export extends false finally for from function if implements import in instanceof interface let new null of private protected public readonly return static super switch this throw true try type typeof undefined var void while with yield'.split(
    ' ',
  ),
)
const PY_KEYWORDS = new Set(
  'and as assert async await break class continue def del elif else except False finally for from global if import in is lambda None nonlocal not or pass raise return True try while with yield'.split(
    ' ',
  ),
)

interface Rule {
  type: TokenType
  re: RegExp
}

const jsRules: Rule[] = [
  { type: 'comment', re: /\/\/[^\n]*/y },
  { type: 'comment', re: /\/\*[\s\S]*?(?:\*\/|$)/y },
  { type: 'string', re: /`(?:\\[\s\S]|[^`\\])*`?/y },
  { type: 'string', re: /'(?:\\.|[^'\\\n])*'?/y },
  { type: 'string', re: /"(?:\\.|[^"\\\n])*"?/y },
  { type: 'number', re: /\b\d[\d_]*(?:\.\d+)?\b/y },
  { type: 'plain', re: /[A-Za-z_$][\w$]*/y },
  { type: 'punctuation', re: /[{}()[\];,.<>:=+\-*/!?&|%^~]+/y },
]

const pyRules: Rule[] = [
  { type: 'comment', re: /#[^\n]*/y },
  { type: 'string', re: /"""[\s\S]*?(?:"""|$)/y },
  { type: 'string', re: /'(?:\\.|[^'\\\n])*'?/y },
  { type: 'string', re: /"(?:\\.|[^"\\\n])*"?/y },
  { type: 'number', re: /\b\d[\d_]*(?:\.\d+)?\b/y },
  { type: 'plain', re: /[A-Za-z_][\w]*/y },
  { type: 'punctuation', re: /[{}()[\];,.<>:=+\-*/!@&|%^~]+/y },
]

const jsonRules: Rule[] = [
  { type: 'property', re: /"(?:\\.|[^"\\\n])*"(?=\s*:)/y },
  { type: 'string', re: /"(?:\\.|[^"\\\n])*"?/y },
  { type: 'number', re: /-?\b\d+(?:\.\d+)?\b/y },
  { type: 'keyword', re: /\b(?:true|false|null)\b/y },
  { type: 'punctuation', re: /[{}[\],:]/y },
]

function tokenize(source: string, rules: Rule[], keywords: Set<string>): Token[] {
  const tokens: Token[] = []
  let i = 0
  let plain = ''
  const flush = () => {
    if (plain) tokens.push({ type: 'plain', value: plain })
    plain = ''
  }

  outer: while (i < source.length) {
    for (const rule of rules) {
      rule.re.lastIndex = i
      const m = rule.re.exec(source)
      if (m && m[0].length > 0) {
        let type = rule.type
        const value = m[0]
        if (type === 'plain') {
          if (keywords.has(value)) type = 'keyword'
          else if (source[i + value.length] === '(') type = 'function'
          else if (source[i - 1] === '.') type = 'property'
        }
        flush()
        tokens.push({ type, value })
        i += value.length
        continue outer
      }
    }
    plain += source[i]
    i++
  }
  flush()
  return tokens
}

function highlightMarkdown(source: string): Token[] {
  return source.split(/(\n)/).flatMap<Token>((line) => {
    if (/^#{1,6}\s/.test(line)) return [{ type: 'keyword', value: line }]
    if (/^\s*>/.test(line)) return [{ type: 'comment', value: line }]
    if (/^ {4}|^\t/.test(line)) return [{ type: 'string', value: line }]
    if (/^\s*[*-]\s/.test(line)) {
      const [, bullet, rest] = /^(\s*[*-]\s)(.*)$/.exec(line)!
      return [
        { type: 'punctuation', value: bullet },
        { type: 'plain', value: rest },
      ]
    }
    return [{ type: 'plain', value: line }]
  })
}

/** Highlight source and split it into per-line token arrays. */
export function highlightLines(source: string, language: CodeLanguage): Token[][] {
  let tokens: Token[]
  switch (language) {
    case 'typescript':
    case 'tsx':
    case 'javascript':
      tokens = tokenize(source, jsRules, JS_KEYWORDS)
      break
    case 'python':
      tokens = tokenize(source, pyRules, PY_KEYWORDS)
      break
    case 'json':
      tokens = tokenize(source, jsonRules, new Set())
      break
    case 'markdown':
      tokens = highlightMarkdown(source)
      break
    default:
      tokens = [{ type: 'plain', value: source }]
  }

  const lines: Token[][] = [[]]
  for (const token of tokens) {
    const parts = token.value.split('\n')
    parts.forEach((part, idx) => {
      if (idx > 0) lines.push([])
      if (part) lines[lines.length - 1].push({ type: token.type, value: part })
    })
  }
  return lines
}

export const TOKEN_CLASS: Record<TokenType, string> = {
  keyword: 'text-syntax-keyword',
  string: 'text-syntax-string',
  comment: 'text-syntax-comment italic',
  number: 'text-syntax-number',
  function: 'text-syntax-function',
  property: 'text-syntax-property',
  punctuation: 'text-syntax-punctuation',
  plain: '',
}

export function languageFromFence(lang: string | undefined): CodeLanguage {
  switch (lang) {
    case 'ts':
    case 'typescript':
      return 'typescript'
    case 'tsx':
      return 'tsx'
    case 'js':
    case 'javascript':
    case 'jsx':
      return 'javascript'
    case 'py':
    case 'python':
      return 'python'
    case 'json':
      return 'json'
    default:
      return 'text'
  }
}
