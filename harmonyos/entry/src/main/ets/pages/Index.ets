import { webview } from '@kit.ArkWeb';

const SERVER_URL_KEY = 'youphonium.serverUrl';
const SERVER_CONFIGURED_KEY = 'youphonium.serverConfigured';
const DEFAULT_SERVER_URL = 'http://192.168.1.2:8000';

PersistentStorage.persistProp<string>(SERVER_URL_KEY, DEFAULT_SERVER_URL);
PersistentStorage.persistProp<boolean>(SERVER_CONFIGURED_KEY, false);

@Entry
@Component
struct Index {
  private controller: webview.WebviewController = new webview.WebviewController();

  @StorageLink(SERVER_URL_KEY) private serverUrl: string = DEFAULT_SERVER_URL;
  @StorageLink(SERVER_CONFIGURED_KEY) private serverConfigured: boolean = false;
  @StorageLink('youphonium.appInForeground')
  @Watch('onForegroundChanged') private appInForeground: boolean = true;

  @State private draftServerUrl: string = DEFAULT_SERVER_URL;
  @State private showSettings: boolean = false;
  @State private pageReady: boolean = false;
  @State private pageLoading: boolean = false;
  @State private errorMessage: string = '';

  aboutToAppear(): void {
    this.draftServerUrl = this.serverUrl;
    this.showSettings = !this.serverConfigured;
  }

  private normalizedUrl(value: string): string {
    let normalized = value.trim();
    if (!normalized.startsWith('http://') && !normalized.startsWith('https://')) {
      normalized = `http://${normalized}`;
    }
    while (normalized.endsWith('/')) {
      normalized = normalized.substring(0, normalized.length - 1);
    }
    return normalized;
  }

  private connect(): void {
    const normalized = this.normalizedUrl(this.draftServerUrl);
    if (normalized === 'http://' || normalized === 'https://') {
      this.errorMessage = 'Enter the address shown by the YouPhonium server.';
      return;
    }
    this.serverUrl = normalized;
    this.draftServerUrl = normalized;
    this.serverConfigured = true;
    this.showSettings = false;
    this.errorMessage = '';
    this.pageLoading = true;
    this.controller.loadUrl(normalized);
  }

  private reload(): void {
    this.errorMessage = '';
    this.pageLoading = true;
    this.controller.refresh();
  }

  private runLifecycleScript(method: string): void {
    if (!this.pageReady) {
      return;
    }
    const source = `window.YouPhoniumApp && window.YouPhoniumApp.${method}()`;
    this.controller.runJavaScript(source).catch(() => {
      // The page may be navigating or its process may have been reclaimed.
    });
  }

  private onForegroundChanged(): void {
    if (this.appInForeground) {
      this.runLifecycleScript('onHostForeground');
    } else {
      this.runLifecycleScript('onHostBackground');
    }
  }

  @Builder
  private connectionPanel(): void {
    Column({ space: 16 }) {
      Text('Connect to YouPhonium')
        .fontSize(24)
        .fontWeight(FontWeight.Bold)
        .fontColor('#2F2540')

      Text('Start YouPhonium on a computer connected to the same trusted Wi-Fi network, then enter the address printed by the launcher.')
        .fontSize(15)
        .fontColor('#625B6B')
        .lineHeight(22)

      TextInput({
        text: this.draftServerUrl,
        placeholder: 'http://192.168.1.20:8000'
      })
        .type(InputType.Normal)
        .enterKeyType(EnterKeyType.Go)
        .height(48)
        .onChange((value: string) => {
          this.draftServerUrl = value;
          this.errorMessage = '';
        })
        .onSubmit(() => this.connect())

      if (this.errorMessage.length > 0) {
        Text(this.errorMessage)
          .fontSize(14)
          .fontColor('#B42318')
          .width('100%')
      }

      Button('Connect')
        .width('100%')
        .height(48)
        .backgroundColor('#5B3F8C')
        .onClick(() => this.connect())

      if (this.serverConfigured) {
        Button('Cancel')
          .width('100%')
          .height(44)
          .fontColor('#5B3F8C')
          .backgroundColor('#EEE9F5')
          .onClick(() => {
            this.draftServerUrl = this.serverUrl;
            this.errorMessage = '';
            this.showSettings = false;
          })
      }
    }
    .width('100%')
    .padding(24)
    .backgroundColor('#F7F5FA')
  }

  build() {
    Stack({ alignContent: Alignment.Top }) {
      Column() {
        Row({ space: 10 }) {
          Text('YouPhonium')
            .fontSize(18)
            .fontWeight(FontWeight.Bold)
            .fontColor('#2F2540')

          Blank()

          if (this.pageLoading) {
            LoadingProgress()
              .width(20)
              .height(20)
              .color('#5B3F8C')
          }

          Button('Reload')
            .fontSize(13)
            .height(34)
            .fontColor('#5B3F8C')
            .backgroundColor('#EEE9F5')
            .onClick(() => this.reload())

          Button('Server')
            .fontSize(13)
            .height(34)
            .fontColor('#5B3F8C')
            .backgroundColor('#EEE9F5')
            .onClick(() => {
              this.draftServerUrl = this.serverUrl;
              this.showSettings = true;
            })
        }
        .width('100%')
        .height(52)
        .padding({ left: 16, right: 12 })
        .backgroundColor('#F7F5FA')

        Web({
          src: this.serverConfigured ? this.serverUrl : 'about:blank',
          controller: this.controller
        })
          .javaScriptAccess(true)
          .domStorageAccess(true)
          .fileAccess(true)
          .onlineImageAccess(true)
          .zoomAccess(true)
          .onPageBegin(() => {
            this.pageReady = false;
            this.pageLoading = true;
            this.errorMessage = '';
          })
          .onPageEnd(() => {
            this.pageReady = true;
            this.pageLoading = false;
            if (!this.appInForeground) {
              this.runLifecycleScript('onHostBackground');
            }
          })
          .onErrorReceive((event) => {
            this.pageReady = false;
            this.pageLoading = false;
            const detail = event.error.getErrorInfo();
            this.errorMessage = detail.length > 0
              ? `Could not reach ${this.serverUrl}: ${detail}`
              : `Could not reach ${this.serverUrl}.`;
          })
          .width('100%')
          .layoutWeight(1)
      }
      .width('100%')
      .height('100%')

      if (this.errorMessage.length > 0 && !this.showSettings) {
        Column({ space: 14 }) {
          Text('Server unavailable')
            .fontSize(22)
            .fontWeight(FontWeight.Bold)
          Text(this.errorMessage)
            .fontSize(14)
            .fontColor('#625B6B')
          Button('Try again')
            .width('100%')
            .backgroundColor('#5B3F8C')
            .onClick(() => this.reload())
          Button('Change server')
            .width('100%')
            .fontColor('#5B3F8C')
            .backgroundColor('#EEE9F5')
            .onClick(() => this.showSettings = true)
        }
        .width('88%')
        .padding(24)
        .margin({ top: 90 })
        .borderRadius(18)
        .backgroundColor('#F7F5FA')
      }

      if (this.showSettings) {
        Column() {
          this.connectionPanel()
        }
        .width('100%')
        .height('100%')
        .justifyContent(FlexAlign.Center)
        .padding(20)
        .backgroundColor('#F7F5FA')
      }
    }
    .width('100%')
    .height('100%')
    .backgroundColor('#FFFFFF')
  }
}
