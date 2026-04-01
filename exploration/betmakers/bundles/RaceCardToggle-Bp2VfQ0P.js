import{j as t,aO as g}from"./main-BL07p92A.js";const d=({theme:r="default",type:o="button",className:a="",disabled:e,showRedDot:l=!1,children:s,...i})=>t.jsxs("button",{className:g("text-text-primary relative z-10 my-2 rounded-3xl border px-3 py-2 text-xs font-semibold",{"border-border-primary text-text/toggle/default bg-bg-toggle-default":!e&&r==="default","bg-bg-toggle-active text-text-toggle-active border-border-toggle-active":!e&&r==="selected","bg-surface-light cursor-not-allowed":e},a),type:o,disabled:e,...i,children:[s,l&&t.jsx("div",{className:"border-border-primary bg-ui-red-normal absolute -right-0 -top-1 h-3 w-3 rounded-full border",style:{animation:"blink 2s ease-in-out infinite"}}),t.jsx("style",{children:`
          @keyframes blink {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
          }
        `})]});export{d as R};
