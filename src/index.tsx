import {
  PanelSection,
  PanelSectionRow,
  ToggleField,
  SliderField,
  Field,
  staticClasses,
} from "@decky/ui";
import { callable, definePlugin } from "@decky/api";
import { useEffect, useRef, useState } from "react";
import { FaMousePointer } from "react-icons/fa";

// RPC al backend Python (main.py)
const getState = callable<[], any>("get_state");
const setEnabled = callable<[enabled: boolean], any>("set_enabled");
const setSetting = callable<[key: string, value: any], any>("set_setting");

function Content() {
  const [state, setState] = useState<any>(null);
  const [speed, setSpeed] = useState<number | null>(null);
  const speedTimer = useRef<any>(null);

  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      try {
        const s = await getState();
        if (alive) setState(s);
      } catch (e) {
        /* backend no disponible aún */
      }
    };
    refresh();
    const id = setInterval(refresh, 2000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  if (!state) {
    return (
      <PanelSection>
        <PanelSectionRow>
          <Field label="Cargando…" />
        </PanelSectionRow>
      </PanelSection>
    );
  }

  const s = state.settings;
  const st = state.status;
  const on = !!s.enabled && !!st.running;
  const statusText = st.error
    ? `Error: ${st.error}`
    : on
    ? `Activo: ${st.touch_name ?? st.touch_path ?? "?"}`
    : "Apagado (touchscreen normal)";

  const commitSpeed = (v: number) => {
    setSpeed(v);
    if (speedTimer.current) clearTimeout(speedTimer.current);
    speedTimer.current = setTimeout(async () => {
      setState(await setSetting("speed", v));
      setSpeed(null);
    }, 400);
  };

  const toggleSetting = (key: string) => async (v: boolean) => {
    setState(await setSetting(key, v));
  };

  return (
    <PanelSection title="Touchscreen como mouse">
      <PanelSectionRow>
        <ToggleField
          label="Pantalla como mouse"
          description="Solo mueve el cursor; nunca hace clicks. Apagar devuelve el touchscreen."
          checked={on}
          onChange={async (v: boolean) => setState(await setEnabled(v))}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <Field label={statusText} />
      </PanelSectionRow>
      <PanelSectionRow>
        <SliderField
          label="Velocidad"
          value={speed ?? s.speed}
          min={0.2}
          max={4}
          step={0.1}
          showValue
          onChange={commitSpeed}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField
          label="Intercambiar ejes X/Y"
          description="Si el cursor se mueve en el eje equivocado"
          checked={!!s.swap_xy}
          onChange={toggleSetting("swap_xy")}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField
          label="Invertir X"
          checked={!!s.flip_x}
          onChange={toggleSetting("flip_x")}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField
          label="Invertir Y"
          checked={!!s.flip_y}
          onChange={toggleSetting("flip_y")}
        />
      </PanelSectionRow>
    </PanelSection>
  );
}

export default definePlugin(() => {
  return {
    name: "TouchMouse",
    titleView: <div className={staticClasses.Title}>TouchMouse</div>,
    content: <Content />,
    icon: <FaMousePointer />,
  };
});
