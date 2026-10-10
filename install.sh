#!/bin/bash
# Qlimen 安装脚本
#
#   ./install.sh                         预演：只打印会做什么，什么都不写
#   ./install.sh --apply                 生成本机配置（只写本仓库目录和工作区里的工人目录）
#   ./install.sh --apply --link claude,codex,dsh,kimi
#                                        另外在 ~/.claude、~/.codex、~/.dsh、~/.kimi-code 里建软链接（已存在的一律不动）
#   选项：--workspace <目录>   派工允许的工作区（默认：本仓库的上一级目录）
#         --force              重新生成已经存在的 预算.json / 保护清单.txt（默认保留你改过的）
#
# 它不会改你的 ~/.claude/settings.json、~/.claude/CLAUDE.md、~/.codex/hooks.json、~/.dsh/profiles：
# 要加的内容打印出来，由你自己粘贴。也不会加锁：确认一切正常后，你自己运行 core/bin/qlimen-lock。
set -u

QLIMEN_HOME="$(cd "$(dirname "$0")" && pwd -P)"
WORKSPACE="$(dirname "$QLIMEN_HOME")"
APPLY=0; FORCE=0; LINKS=""

while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --force) FORCE=1 ;;
    --link) shift; LINKS="${1:-}" ;;
    --workspace) shift; WORKSPACE="$(cd "${1:-.}" 2>/dev/null && pwd -P)" || { echo "工作区目录不存在：${1:-}" >&2; exit 2; } ;;
    -h|--help) sed -n 2,14p "$0"; exit 0 ;;
    *) echo "不认识的参数：$1（用 --help 看用法）" >&2; exit 2 ;;
  esac
  shift
done

say() { if [ $APPLY = 1 ]; then echo "  $*"; else echo "  [预演] $*"; fi; }
fail=0

echo "Qlimen 安装（$([ $APPLY = 1 ] && echo 实装 || echo '预演，加 --apply 才会写')）"
echo "  QLIMEN_HOME = $QLIMEN_HOME"
echo "  工作区      = $WORKSPACE"
case "$QLIMEN_HOME/" in "$WORKSPACE"/*) ;; *) echo "  提醒：Qlimen 不在工作区里面。派工脚本的禁区（core/workers、core/data）照样生效，只是不在允许范围内。" ;; esac

[ "$(uname)" = "Darwin" ] || echo "  提醒：不是 macOS。qlimen-lock 用的 chflags 和工人写文件用的 sandbox-exec 只在 macOS 上有，其余部分能用。"
PY=/usr/bin/python3; [ -x "$PY" ] || PY="$(command -v python3 || true)"
[ -n "$PY" ] || { echo "  ✗ 找不到 python3（guard、waibao、钩子脚本都要它）" >&2; exit 1; }

# 用 Python 填模板（不用 sed，路径里的 & | 不会被当成替换语法）。钩子命令里的路径在模板中已加单引号，
# 所以路径可以有空格；路径里有单引号的话钩子命令会断，换个目录名
fill() {  # fill <模板> <输出>
  "$PY" - "$1" "$2" "$QLIMEN_HOME" "$WORKSPACE" <<'PYEOF'
import json, sys
src, dst, home, ws = sys.argv[1:5]
if dst.endswith(".json"):   # 写进 JSON 字符串的路径要转义（反斜杠、双引号）
    home, ws = json.dumps(home)[1:-1], json.dumps(ws)[1:-1]
text = open(src, encoding="utf-8").read().replace("{{QLIMEN_HOME}}", home).replace("{{WORKSPACE}}", ws)
open(dst, "w", encoding="utf-8").write(text)
PYEOF
}

gen() {  # gen <模板> <输出> <是否保留已有>
  if [ -e "$2" ] && [ "$3" = keep ] && [ $FORCE = 0 ]; then
    echo "  保留已有的 ${2#$QLIMEN_HOME/}（要重新生成加 --force）"; return
  fi
  say "生成 ${2#$QLIMEN_HOME/}"
  [ $APPLY = 1 ] && { fill "$1" "$2" || { echo "  ✗ 生成失败：$2" >&2; fail=1; }; }
}

echo
echo "1. 本机配置"
gen "$QLIMEN_HOME/core/workers/预算.example.json"        "$QLIMEN_HOME/core/workers/预算.json" keep
gen "$QLIMEN_HOME/core/bin/保护清单.example.txt"          "$QLIMEN_HOME/core/bin/保护清单.txt" keep
for a in claude codex dsh; do
  gen "$QLIMEN_HOME/adapters/$a/hooks.example.json" "$QLIMEN_HOME/adapters/$a/hooks.json" new
done
gen "$QLIMEN_HOME/adapters/dsh/cordis-hooks.patch.example.yml" "$QLIMEN_HOME/adapters/dsh/cordis-hooks.patch.yml" new

echo
echo "2. 目录和可执行权限"
DIRS=("$QLIMEN_HOME/core/data/工人结果" "$WORKSPACE/工人家.nosync/dsh" "$WORKSPACE/工人家.nosync/kimi/sessions")
for d in "${DIRS[@]}"; do
  [ -d "$d" ] && continue
  say "新建 $d"
  [ $APPLY = 1 ] && mkdir -p "$d"
done
EXE=("$QLIMEN_HOME/core/workers/waibao" "$QLIMEN_HOME/core/workers/guard" "$QLIMEN_HOME/core/bin/qlimen-lock"
     "$QLIMEN_HOME/core/bin/qlimen-unlock" "$QLIMEN_HOME/adapters/dsh/hooks/intake" "$QLIMEN_HOME/adapters/dsh/hooks/private-protocol")
say "给 ${#EXE[@]} 个脚本加可执行权限"
[ $APPLY = 1 ] && chmod +x "${EXE[@]}"

link() {  # link <目标> <链接位置>
  if [ -e "$2" ] || [ -L "$2" ]; then
    if [ "$(readlink "$2" 2>/dev/null)" = "$1" ]; then echo "  已经链好：$2"
    else echo "  ✗ 跳过：$2 已存在且不是指向 Qlimen 的链接。先备份或删掉它，再重跑。"; fail=1; fi
    return
  fi
  say "链接 $2 → $1"
  [ $APPLY = 1 ] && { mkdir -p "$(dirname "$2")" && ln -s "$1" "$2" || fail=1; }
}

if [ -n "$LINKS" ]; then
  echo
  echo "3. 软链接（$LINKS）"
  CORE="$QLIMEN_HOME/core/rules/AGENTS.md"; SK="$QLIMEN_HOME/core/skills/waibao"
  IFS=',' read -r -a WANT <<< "$LINKS"
  for t in "${WANT[@]}"; do
    case "$t" in
      claude) link "$CORE" "$HOME/.claude/qlimen-AGENTS.md"; link "$SK" "$HOME/.claude/skills/waibao" ;;
      codex)  link "$CORE" "$HOME/.codex/AGENTS.md";         link "$SK" "$HOME/.codex/skills/waibao" ;;
      dsh)    link "$CORE" "$HOME/.dsh/AGENTS.md";           link "$SK" "$HOME/.dsh/skills/waibao" ;;
      kimi)   link "$CORE" "$HOME/.kimi-code/AGENTS.md" ;;
      *) echo "  ✗ 不认识的环境：$t（可选 claude,codex,dsh,kimi）"; fail=1 ;;
    esac
  done
fi

cat <<EOF

接下来要你自己做的（按你用的工具挑）：

【先设一个变量】文档和工人名册里写的 \$QLIMEN_HOME 就是本仓库的绝对路径。建议加进 ~/.zshrc（或你的 shell 配置）：
    export QLIMEN_HOME="$QLIMEN_HOME"
  注意：桌面应用里的代理不一定读 shell 配置，给它们的命令最好直接写绝对路径（下面打印的都已经是绝对路径）。

【Claude Code】
  1) ~/.claude/CLAUDE.md 里加两行：
       @~/.claude/qlimen-AGENTS.md
       @~/.claude/private-protocol.md
  2) 私有协议起步：cp "$QLIMEN_HOME/examples/私有协议/claude-code.md" ~/.claude/private-protocol.md，再按你的情况改
  3) ~/.claude/settings.json 里并进下面两段（已有 hooks / permissions 的话合并进去）：
       "hooks": { "PreToolUse": [ { "matcher": "Bash", "hooks": [ { "type": "command", "command": "'$QLIMEN_HOME/core/workers/guard'", "timeout": 5 } ] } ] }
       "permissions": { "allow": [ "Bash($QLIMEN_HOME/core/workers/waibao *)" ] }

【Codex】
  cp "$QLIMEN_HOME/adapters/codex/hooks.json" ~/.codex/hooks.json（已有就合并），再在 Codex 里用 /hooks 信任它；
  私有协议起步：cp "$QLIMEN_HOME/examples/私有协议/codex.md" ~/.codex/private-protocol.md

【DSH】
  把 "$QLIMEN_HOME/adapters/dsh/cordis-hooks.patch.yml" 的内容追加到 ~/.dsh/profiles/desktop/cordis.patch.yml 末尾，重启 DSH；
  私有协议起步：cp "$QLIMEN_HOME/examples/私有协议/dsh.md" ~/.dsh/private-protocol.md

【工人（可选，要有 DSH / kimi-code）】
  工人用自己的数据目录，只放登录要用的最少文件（作者的做法；不要整目录复制，那样会把你全部会话记录也带过去）：
    DSH：  cp ~/.dsh/.credentials.yaml "$WORKSPACE/工人家.nosync/dsh/"
           mkdir -p "$WORKSPACE/工人家.nosync/dsh/profiles" && cp -R ~/.dsh/profiles/headless "$WORKSPACE/工人家.nosync/dsh/profiles/"
    kimi： cp -R ~/.kimi-code/credentials ~/.kimi-code/device_id "$WORKSPACE/工人家.nosync/kimi/"
           ln -s ~/.kimi-code/config.toml "$WORKSPACE/工人家.nosync/kimi/config.toml"
  登录过期时重新复制凭据。这个目录在派工禁区里，只能防止把工人「派进去」，防不了工人用读文件工具读到；
  工人读到的内容会发给模型服务商。所以这里只放凭据，别放别的东西，也别放进会同步、会外发的地方。
  试一下（不花钱）："$QLIMEN_HOME/core/workers/waibao" --status

【最后】
  跑测试（不调模型）：python3 "$QLIMEN_HOME/tests/跑全部测试.py"
  一切正常后加锁：  "$QLIMEN_HOME/core/bin/qlimen-lock"
  要改受保护的文件时先解锁：  "$QLIMEN_HOME/core/bin/qlimen-unlock"，改完再加锁。
  加锁后：Claude 应用自己在设置里改的东西存不进 settings.json，kimi login 会报 EPERM，重跑 install.sh 生成配置也会失败——都要先解锁。
EOF

[ $fail = 0 ] || { echo; echo "有 ✗ 的步骤没做，见上面。"; exit 1; }
exit 0
