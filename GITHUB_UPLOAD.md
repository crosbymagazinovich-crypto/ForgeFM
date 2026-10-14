# Загрузка на GitHub

Репозиторий ForgeFM не был указан, поэтому этот архив никуда не загружен.

1. Скопируйте `docs/screenshots/` и `tools/capture-gtk4.sh` в корень своего
   git-репозитория ForgeFM.
2. Добавьте раздел «Скриншоты» из `README.md` в README репозитория.
3. Выполните команды:

```bash
git add docs/screenshots tools/capture-gtk4.sh README.md
git commit -m "docs: add ForgeFM screenshots"
git push
```

Для актуального GTK4-интерфейса на Gentoo выполните
`./tools/capture-gtk4.sh` при открытом окне ForgeFM и сфокусируйтесь на нем
в течение 5 секунд. Затем добавьте `docs/screenshots/forgefm-gtk4.png`
в репозиторий и README.
