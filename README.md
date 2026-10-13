# Tailscale GUI

Interface gráfica nativa **GTK4 / libadwaita** para controlar o [Tailscale](https://tailscale.com) no Linux (Fedora).

<img src="tailscale-gui.png" alt="Tailscale GUI: símbolo de nove pontos do Tailscale" width="160">

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
- **Tray** — opção de minimizar para a bandeja do sistema ao fechar a janela, com menu para abrir o app e sair

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
sudo dnf install ./tailscale-gui-1.2.1-*.noarch.rpm
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
sudo dnf install ./tailscale-gui-1.2.1-*.noarch.rpm
```

## Executar sem instalar

```bash
/usr/bin/python3 tailscale-gui.py
```

Execute o app como seu usuário habitual. A interface solicita autorização somente quando uma alteração for negada pelo daemon. Se o estado for **Login necessário**, clique em **Conectar** e depois no link de autenticação. Dispositivos com aprovação pendente precisam ser liberados no console de administração do Tailnet.

As opções são lidas de `tailscale debug prefs`. Se essa consulta falhar, o app informa o erro e desabilita os controles cujos valores não podem ser confirmados. Ao reconectar, `tailscale up` é executado sem flags; alterações individuais usam `tailscale set`, conforme a [documentação do Tailscale](https://tailscale.com/docs/reference/tailscale-cli/up).

## Minimizar para o tray

Em **Configurações → Comportamento do aplicativo**, habilite **Minimizar para o tray ao fechar**. A opção começa desativada e é salva em `~/.config/tailscale-gui/settings.json` (ou no diretório definido por `XDG_CONFIG_HOME`). Quando ativada, fechar a janela mantém o aplicativo e a atualização do status funcionando. Clique no ícone para reabrir a janela ou use **Sair** no menu do tray ou do app; `Ctrl+Q` também encerra o aplicativo.

A integração usa StatusNotifierItem e DBusMenu, sem adicionar dependências GTK3. No KDE Plasma, o suporte já faz parte do painel. No GNOME, é necessário suporte a AppIndicator/KStatusNotifierItem, como a extensão **AppIndicator and KStatusNotifierItem Support**. A opção fica indisponível se não houver um host de tray ativo. Nesse caso, fechar encerra o app normalmente; se o suporte ao tray desaparecer enquanto a janela estiver oculta, ela será reaberta automaticamente.

O estado da conexão continua sendo consultado a cada 5 segundos com a janela oculta. A versão 1.2.1 corrige a atualização do rótulo do menu e do tooltip para que hosts que mantêm o menu em cache, como o GNOME, acompanhem as transições entre conectado e desconectado. Depois de atualizar o RPM, use **Sair** e reabra o app para carregar a correção.

O ícone usa o padrão de nove pontos da [identidade do Tailscale](https://github.com/tailscale/tailscale/blob/main/client/web/src/assets/icons/tailscale-logo.svg), com uma versão compacta própria para o painel. Esta interface é um projeto independente.

## Verificação

```bash
/usr/bin/python3 -m unittest discover -s tests -v
desktop-file-validate tailscale-gui.desktop
bash -n build-rpm.sh
```

Os testes simulam todas as chamadas ao Tailscale e cobrem falhas, timeouts, autorização, autenticação, leitura de preferências, Exit Nodes com nomes duplicados, navegação e atualizações concorrentes. Os testes GTK exigem um display; sem ele, essa parte da suíte é marcada como ignorada. Para um display isolado, use GTK Broadway ou Xvfb.

Os testes do protocolo de tray usam um painel simulado em um barramento DBus privado:

```bash
TAILSCALE_GUI_TEST_DBUS=1 dbus-run-session /usr/bin/python3 -m unittest discover -s tests -v
```

Eles verificam o registro do ícone, o menu, a reabertura, o encerramento, a perda e o retorno do host de tray, além da persistência da preferência. Para incluir também os testes de janela, execute com um display GTK disponível.

## Licença

MIT
