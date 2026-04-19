# Tailscale GUI

Interface gráfica nativa **GTK4 / libadwaita** para controlar o [Tailscale](https://tailscale.com) no Linux (Fedora).

![Tailscale GUI Icon](tailscale-gui.png)

## Funcionalidades

- **Status** — estado da conexão, IP Tailscale, hostname, relay DERP e nome da rede
- **Peers** — lista de dispositivos do Tailnet com status online/offline em tempo real e indicação de Exit Node
- **Configurações** — seletor de Exit Node, toggles para Accept Routes, SSH e Shields Up
- **Auto-refresh** a cada 5 segundos
- **Log** das operações de connect/disconnect

## Requisitos

- Fedora 44+ (ou qualquer distro com GTK4 + libadwaita)
- `tailscale` instalado e configurado
- `python3` com `python3-gobject`
- `gtk4` e `libadwaita`

## Instalação via RPM

Baixe o `.rpm` na [página de releases](../../releases) e instale com:

```bash
sudo dnf install ./tailscale-gui-*.rpm
```

## Build manual

```bash
# Clonar o repositório
git clone https://github.com/mca-afk/tailscale-gui.git
cd tailscale-gui

# Gerar o RPM
bash build-rpm.sh

# Instalar
sudo dnf install ./tailscale-gui-*.rpm
```

## Executar sem instalar

```bash
/usr/bin/python3 tailscale-gui.py
```

## Licença

MIT
