/** 生成「让 AI 助手（豆包等）帮我在本机安装」的提示词，供用户一键复制到 AI 对话里。
 *
 * 站内不承担安装执行：把项目上下文（仓库地址、系统要求、国内镜像加速地址）整理成一段结构化指令，
 * 用户粘贴给能操作电脑的 AI 助手（本地电脑模式），由对方完成下载、装环境、运行。
 */

/** 拼提示词所需的最小项目信息（Project / ProjectDetail 都满足） */
export interface InstallPromptSource {
  name: string
  description: string
  html_url: string
  system_requirements: string[]
  /** 国内镜像前缀（gh-proxy 系列）：直连 GitHub 中断时用「前缀 + 原始链接」加速下载 */
  mirror_prefixes?: string[]
  /** 发布页安装包：仅在提示词里取一个做真实的镜像示例链接 */
  assets?: { name: string; url: string; platform?: string }[]
}

/** 单行化 + 截断：简介可能是多行英文长文，整段塞进提示词会喧宾夺主 */
function oneLine(s: string, max = 80): string {
  const t = s.replace(/\s+/g, ' ').trim()
  return t.length > max ? `${t.slice(0, max)}…` : t
}

/** 挑一个真实链接给镜像示例：优先当前系统的安装包，其次任意安装包，最后退回发布页 */
function pickMirrorExample(
  p: InstallPromptSource,
  sys: string,
): { url: string; label: string; matched: boolean } {
  const assets = (p.assets || []).filter((a) => a.url?.startsWith('https://github.com/'))
  const hit = sys ? assets.find((a) => a.platform === sys) : undefined
  const pick = hit || assets[0]
  if (pick) return { url: pick.url, label: oneLine(pick.name, 46), matched: !!hit }
  return { url: `${p.html_url}/releases`, label: '发布页', matched: false }
}

export function buildInstallPrompt(p: InstallPromptSource, os: string): string {
  const lines = [
    '请用「本地电脑模式」（或你能直接操作我电脑的模式）帮我在我的电脑上安装这个开源软件，目标是装好后能正常启动使用。',
    '',
    '软件信息：',
    `- 名称：${p.name || '（见仓库地址）'}`,
  ]
  const desc = oneLine(p.description || '')
  if (desc) lines.push(`- 简介：${desc}`)
  lines.push(`- 仓库地址：${p.html_url}`)
  const reqs = (p.system_requirements || []).filter(Boolean).slice(0, 4).map((r) => oneLine(r, 30))
  if (reqs.length) lines.push(`- 系统要求：${reqs.join('；')}`)
  lines.push('')
  // 只信桌面系统：手机/未知时不写死，让助手先问，避免它按错误系统找包
  const sys = ['Windows', 'macOS', 'Linux'].includes(os) ? os : ''
  lines.push(sys ? `我的系统：${sys}（如与你判断的不一致，请先和我确认）` : '我的系统：请先问我确认后再开始')

  const prefixes = (p.mirror_prefixes || []).filter((x) => x.startsWith('https://')).slice(0, 3)
  if (prefixes.length) {
    const ex = pickMirrorExample(p, sys)
    lines.push(
      '',
      'GitHub 访问加速（重要）：我在国内，直连 GitHub 经常中断或超时，下载文件、打开仓库页/README/Release 页都优先用加速链接。',
      `- 拼法：镜像前缀 + 完整原链接。以${ex.matched && sys ? ` ${sys} 版安装包` : ''} ${ex.label} 为例：`,
      `  原链接：${ex.url}`,
      `  加速链接：${prefixes[0]}${ex.url}`,
      `- 可用前缀，前一个失败就换下一个：${prefixes.join('、')}`,
      '- 加速链接只是官方链接的转发，文件仍来自官方仓库；下载源码压缩包同理：「前缀 + 仓库地址 + /archive/refs/heads/<默认分支>.zip」。',
    )
  }

  lines.push(
    '',
    '请按这个顺序执行：',
    '1. 先看仓库 README 和 Release 页，定出最简单的安装方式，优先现成安装包（.exe / .dmg / AppImage 等）。',
    '2. 只从官方仓库/发布页或上面的加速链接下载，不要用其他来源的安装包和脚本。',
    '3. 需要装运行环境（Python / Node / Docker 等）或从源码部署时，按官方说明来，并告诉我额外装了哪些东西。',
    '4. 每一步先说明再动手；需要管理员权限、修改系统设置、删除文件时，先停下问我确认。',
    '5. 同一种做法失败两次就换一种（换前缀、换安装包、换方式），并原样贴出报错信息，不要反复重试。',
    '6. 如果它其实不是能直接安装的软件（如资源清单、需要自行部署的服务），直接告诉我并给使用建议。',
    '7. 装好后告诉我：怎么启动、以后从哪里打开、改过哪些系统设置或环境变量（若有）。',
    '',
    '如果你不能操作我的电脑，就给我一步步的图文指引（含可复制的完整命令），我自己执行。',
  )
  return lines.join('\n')
}

/** 复制到剪贴板：Clipboard API 优先，被拒/不可用时退回 textarea + execCommand */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch { /* 权限被拒 → 走兜底 */ }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.top = '-1000px'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}
