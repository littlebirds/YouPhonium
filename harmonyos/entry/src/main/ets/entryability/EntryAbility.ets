import { AbilityConstant, UIAbility, Want } from '@kit.AbilityKit';
import { hilog } from '@kit.PerformanceAnalysisKit';
import { window } from '@kit.ArkUI';

const DOMAIN = 0x0000;

export default class EntryAbility extends UIAbility {
  onCreate(_want: Want, _launchParam: AbilityConstant.LaunchParam): void {
    AppStorage.setOrCreate<boolean>('youphonium.appInForeground', true);
    hilog.info(DOMAIN, 'YouPhonium', 'HarmonyOS client created');
  }

  onWindowStageCreate(windowStage: window.WindowStage): void {
    windowStage.loadContent('pages/Index', (error) => {
      if (error.code) {
        hilog.error(DOMAIN, 'YouPhonium', 'Failed to load UI: %{public}s', JSON.stringify(error));
      }
    });
  }

  onForeground(): void {
    AppStorage.setOrCreate<boolean>('youphonium.appInForeground', true);
  }

  onBackground(): void {
    AppStorage.setOrCreate<boolean>('youphonium.appInForeground', false);
  }
}
