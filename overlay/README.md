# Локальный Gentoo overlay: app-misc/forgefm-m07

Это опциональная, НЕ проверенная на реальной системе Gentoo рецептура EAPI=8.
Обычную установку без Portage см. в корневом README.md.

```sh
cd forgefm-m07-v0.4.1
sudo mkdir -p /var/db/repos/forgefm-local /etc/portage/repos.conf
sudo cp -a overlay/. /var/db/repos/forgefm-local/
printf '[forgefm-local]\nlocation = /var/db/repos/forgefm-local\n' \
  | sudo tee /etc/portage/repos.conf/forgefm-local.conf
sudo emerge --ask app-misc/forgefm-m07::forgefm-local
forgefm-m07
```

Если версия помечена `~amd64` и Portage требует разрешение keyword:

```sh
sudo mkdir -p /etc/portage/package.accept_keywords
printf 'app-misc/forgefm-m07 ~amd64\n' \
  | sudo tee /etc/portage/package.accept_keywords/forgefm-m07
```

Установка через Portage не заменяет `~/.local/bin/forgefm` из старых версий.
Пользовательская установка (`./install.sh`) и системная через Portage могут
сосуществовать. Для удаления системного пакета:

```sh
sudo emerge --ask --depclean app-misc/forgefm-m07
```

Внимание: это локальный overlay, не официальный пакет Gentoo.
