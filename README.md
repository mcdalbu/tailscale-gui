# Tailscale GUI

Interface gráfica nativa **GTK4 / libadwaita** para controlar o [Tailscale](https://tailscale.com) no Linux (Fedora).

<img src="tailscale-gui.png" alt="Tailscale GUI: dispositivos conectados e escudo de proteção" width="160">

## Funcionalidades

- **Status** — estado da conexão, autenticação pendente, aprovação do dispositivo, IP Tailscale, hostname, relay DERP e nome da rede
- **Peers** — lista de dispositivos do Tailnet com status online/offline em tempo real e indicação de Exit Node
- **Configurações** — seletor de Exit Node por IP, switches sincronizados com as preferências reais para Accept Routes, SSH e Shields Up
- **Auto-refresh** a cada 5 segundos, sem leituras simultâneas nem perda da seleção de Exit Node antes de aplicar
- **Conectar / desconectar** preservando as configurações existentes
- **Autenticação** — link para concluir o login no navegador, inclusive com Headscale
- **Autorização** — diálogo do sistema via `pkexec` quando o daemon exigir privilégios
- **Log** e notificações de sucesso ou falha, com restauração dos controles após erros
- **Ícone** vetorial SVG e PNG transparente de 256 × 256 pixels

## Requisitos

- Fedora 44+ (ou qualquer distro com GTK4 + libadwaita)
- `tailscale` instalado e configurado
- Python 3.10+ com `python3-gobject`
- GTK 4.12+ e libadwaita 1.4+
- `polkit` (`pkexec`) e um agente de autenticação disponível na sessão gráfica para operações que exijam privilégios
- Serviço `tailscaled` em execução

## Instalação via RPM

Baixe o `.rpm` na [página de releases](../../releases) e instale com:

```bash
sudo dnf install ./tailscale-gui-1.1.0-*.noarch.rpm
```

## Build manual

```bash
# Clonar o repositório
git clone https://github.com/mcdalbu/tailscale-gui.git
cd tailscale-gui

# Gerar o RPM
# Se necessário: sudo dnf install rpm-build python3
bash build-rpm.sh

# Instalar
sudo dnf install ./tailscale-gui-1.1.0-*.noarch.rpm
```

## Executar sem instalar

```bash
/usr/bin/python3 tailscale-gui.py
```

Execute o app como seu usuário habitual. A interface solicita autorização somente quando uma alteração for negada pelo daemon. Se o estado for **Login necessário**, clique em **Conectar** e depois no link de autenticação. Dispositivos com aprovação pendente precisam ser liberados no console de administração do Tailnet.

As opções são lidas de `tailscale debug prefs`. Se essa consulta falhar, o app informa o erro e desabilita os controles cujos valores não podem ser confirmados. Ao reconectar, `tailscale up` é executado sem flags; alterações individuais usam `tailscale set`, conforme a [documentação do Tailscale](https://tailscale.com/docs/reference/tailscale-cli/up).

## Verificação

```bash
/usr/bin/python3 -m unittest discover -s tests -v
desktop-file-validate tailscale-gui.desktop
bash -n build-rpm.sh
```

Os testes simulam todas as chamadas ao Tailscale e cobrem falhas, timeouts, autorização, autenticação, leitura de preferências, Exit Nodes com nomes duplicados, navegação e atualizações concorrentes. Os testes GTK exigem um display; sem ele, essa parte da suíte é marcada como ignorada. Para um display isolado, use GTK Broadway ou Xvfb.

## Licença

MIT
