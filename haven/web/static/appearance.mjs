// The native dictionaries remain the single source for palette values.
let catalog, settings={theme:'haven',mode:'system'};
const media=window.matchMedia('(prefers-color-scheme: light)');
const names={'--bg':'HavenCanvasColor','--bg-raise':'HavenSurface1Color','--bg-raise-2':'HavenSurface2Color','--text':'HavenTextColor','--muted':'HavenMutedTextColor','--border':'HavenStrokeColor','--border-strong':'HavenStrokeColor','--teal':'HavenAccentColor','--cyan':'HavenAccentColor','--violet':'HavenVioletColor','--amber':'HavenWarningColor','--red':'HavenDangerColor','--green':'HavenSuccessColor','--teal-dim':'HavenAccentTintColor','--violet-dim':'HavenVioletTintColor','--amber-dim':'HavenWarningTintColor','--red-dim':'HavenDangerTintColor','--green-dim':'HavenSuccessTintColor'};
function cssColor(value) {return value?.length===9?'#'+value.slice(3)+value.slice(1,3):value;}
function apply() {
  const mode=settings.mode==='system'?(media.matches?'light':'dark'):settings.mode;
  document.documentElement.dataset.appearance=mode;
  document.documentElement.dataset.theme=settings.theme;
  document.documentElement.style.colorScheme=mode;
  const palette=catalog?.palettes?.[settings.theme]?.[mode];
  if (palette) for (const [css,token] of Object.entries(names)) document.documentElement.style.setProperty(css,cssColor(palette[token]));
}
async function load() {
  try {
    const [c,s]=await Promise.all([fetch('/theme-catalog.json',{credentials:'same-origin',redirect:'error'}).then(r=>r.json()),fetch('/api/appearance',{credentials:'same-origin',redirect:'error'}).then(r=>r.json())]);
    catalog=c;
    const appearance=s.appearance||s;
    settings={theme:['haven','canopy','ember','mono'].includes(appearance.theme)?appearance.theme:'haven',mode:['system','light','dark'].includes(appearance.mode)?appearance.mode:'system'};
    const select=document.querySelector('#theme-palette'); if(select)select.value=settings.theme;
  } catch (_) {}
  apply();
}
media.addEventListener('change',()=>{if(settings.mode==='system')apply();});
document.addEventListener('click',event=>{const button=event.target.closest('[data-appearance-mode]');if(button){settings.mode=button.dataset.appearanceMode;apply();}});
document.querySelector('#theme-palette')?.addEventListener('change',async event=>{
  settings.theme=event.target.value;apply();
  try {const r=await fetch('/api/appearance',{method:'PUT',credentials:'same-origin',redirect:'error',headers:{'Content-Type':'application/json'},body:JSON.stringify({theme:settings.theme})});if(!r.ok)throw new Error();event.target.title='';}
  catch(_){event.target.title='Applies to this view; shared settings could not be saved.';}
});
void load();
