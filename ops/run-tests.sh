#!/bin/sh
# Честный прогон всего тест-сьюта: каждый файл в ОТДЕЛЬНОМ процессе.
#
# Зачем не просто `pytest crawler/tests/`. Часть тестов подменяет модули в
# sys.modules ещё на импорте (это единственный способ прогнать код, который
# тянет прод-зависимости и сеть): test_replay_pure и test_mute_resilience
# кладут вместо `crawler.core.db` заглушку, чтобы поймать любое касание
# write-пути. Заглушка живёт до конца ПРОЦЕССА, а pytest импортирует все файлы
# в один процесс на этапе сбора — поэтому файлы, идущие следом, получают фальшивый
# модуль. Замер 30.07: общий прогон дал 19 падений и одну ошибку сбора, а
# по-файлово — три настоящих падения. То есть 16 «поломок» были иллюзией
# инструмента, и из-за них месяц не замечали три реальных.
#
# Отдельный процесс на файл ровно это и лечит: заглушка не переживает файл.
#
#   sh ops/run-tests.sh            # весь сьют
#   sh ops/run-tests.sh -q         # только итог и падения
set -u

PY="${PY:-.venv/bin/python3}"
[ -x "$PY" ] || PY=python3
QUIET=0
[ "${1:-}" = "-q" ] && QUIET=1

total=0
failed=0
failed_files=""

for f in crawler/tests/test_*.py; do
    total=$((total + 1))
    out="$("$PY" -m pytest "$f" -q 2>&1)"
    last="$(printf "%s" "$out" | tail -1)"
    case "$last" in
        *failed*|*error*|*Error*)
            failed=$((failed + 1))
            failed_files="${failed_files} $(basename "$f")"
            printf "❌ %-46s %s\n" "$(basename "$f")" "$last"
            printf "%s\n" "$out" | grep -E "^(FAILED|ERROR)" | sed 's/^/     /'
            ;;
        *)
            [ "$QUIET" = "0" ] && printf "✅ %-46s %s\n" "$(basename "$f")" "$last"
            ;;
    esac
done

echo
if [ "$failed" = "0" ]; then
    echo "ВСЁ ЗЕЛЁНОЕ: $total файлов"
    exit 0
fi
echo "ПАДЕНИЙ: $failed из $total файлов —$failed_files"
exit 1
