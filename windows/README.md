# Tailscale GUI para Windows

Versão portátil para Windows 10/11 **x64**, feita com Qt/PySide6. Não exige Python instalado no computador de destino.

## Usar

1. Instale o [cliente oficial Tailscale para Windows](https://tailscale.com/download/windows) e mantenha seu serviço em execução.
2. Extraia o ZIP e execute **TailscaleGUI.exe**.
3. Use **Conectar**. Caso seja solicitado login, clique em **Abrir página de autenticação**.

O executável é um gerenciador independente: usa o `tailscale.exe` instalado no PATH ou em `Program Files\Tailscale`. Ele preserva as preferências ao conectar e aplica alterações individuais com `tailscale set`. O cliente oficial e o serviço de rede continuam sendo necessários.

Se uma alteração for recusada por falta de permissão, encerre o gerenciador e use **Executar como administrador** no menu de contexto do executável. O aplicativo não solicita privilégios ao abrir normalmente.

## Recursos

- Status, IP, hostname, relay, Tailnet e mensagens de saúde.
- Dispositivos online/offline e indicação de Exit Node.
- Seleção de Exit Node por IP, inclusive quando há nomes duplicados.
- Accept Routes e Shields Up sincronizados com o daemon; falhas restauram os controles.
- Atualização automática a cada 5 segundos, inclusive com a janela oculta.
- Tray nativo do Windows com Abrir, Conectar, Desconectar e Sair.
- Opção **Minimizar para o tray ao fechar**, salva em `%APPDATA%\TailscaleGUI\settings.json`.
- Instância única por usuário: abrir novamente traz a janela existente para a frente.
- Ícone com a identidade do Tailscale e executável sem janela de console.

O [servidor Tailscale SSH não é suportado no Windows](https://tailscale.com/docs/features/tailscale-ssh). Por isso, esta versão não oferece um switch que tente habilitá-lo. O OpenSSH do Windows pode ser usado pela rede Tailscale.

O pacote não tem assinatura digital. O código-fonte e os testes estão no repositório [mcdalbu/tailscale-gui](https://github.com/mcdalbu/tailscale-gui). Qt/PySide6 e suas bibliotecas têm licenças próprias, incluídas no diretório `licenses` do ZIP.

## Compilar no Windows

No PowerShell, com Python 3.12 x64 instalado, a partir da raiz do repositório:

```powershell
py -3.12 -m venv .venv-windows
.\.venv-windows\Scripts\python.exe -m pip install -r windows/requirements-build.txt
.\.venv-windows\Scripts\python.exe -m windows.build
```

O build executa os testes, gera um `.exe` portátil com PyInstaller e inicia esse executável com dados simulados para conferir a interface e os recursos empacotados. A saída fica em `dist/windows/TailscaleGUI-Windows-x64-1.3.0.zip`, acompanhada de um SHA-256. A compilação também está disponível no workflow **Windows** do GitHub Actions.

Para rodar a interface diretamente do código, use `python -m windows.launcher`. Para testar sem afetar uma instalação real do Tailscale:

```powershell
python -m unittest discover -s windows/tests -v
python -m windows.launcher --smoke-test
```
