import{r as s,fg as W,ad as E,ac as j,j as e,aO as p,e3 as L}from"./main-BL07p92A.js";import{I as M}from"./FeaturedSgmWidgetHeader-DA7kkoS1.js";const S=()=>s.useContext(W),D=({betbuilder:o})=>{const l=E(),{liveToastData:t}=S(),u=j(r=>r.preference.racingToggles),[g,y]=s.useState(!1),[d,w]=s.useState(0),[m,R]=s.useState(0),[f,c]=s.useState(!0),[i,T]=s.useState(!1),a=s.useRef(null),x=s.useRef(null),h=s.useRef(null),b=s.useRef(null),v=s.useCallback(()=>{if(h.current&&b.current){const r=h.current.offsetWidth,n=b.current.scrollWidth;R(r),w(n),y(!0),T(n>r)}},[]);s.useEffect(()=>{t&&c(!0)},[t]),s.useEffect(()=>{if(t&&f){const r=setTimeout(v,100);return()=>clearTimeout(r)}},[t,f,v]),s.useEffect(()=>{if(!t||!g)return;const r=`${t.type}-${t.title}-${t.description}-3`;if(x.current!==r){if(x.current=r,c(!0),a.current&&clearTimeout(a.current),i){const k=(d+m/2)/25*1e3;a.current=setTimeout(()=>{c(!1)},k+1e3)}else{const n=Math.max(2e3,t.title.length*100+t.description.length*50);a.current=setTimeout(()=>{c(!1)},n)}return()=>{a.current&&clearTimeout(a.current)}}},[t,g,i,d,m]),s.useEffect(()=>()=>{a.current&&clearTimeout(a.current),x.current=null},[]);const N=()=>{t&&l(L({key:"live",value:!u.live}))};if(!t||!f)return null;const C=i?(d+m/2)/50:0;return e.jsxs(e.Fragment,{children:[e.jsx("style",{children:`
        @keyframes racelab-toast-slideUp {
          from {
            transform: translateY(100%);
            opacity: 0;
          }
          to {
            transform: translateY(0);
            opacity: 1;
          }
        }

        @keyframes racelab-toast-marquee {
          0% {
            transform: translateX(0);
          }
          5% {
            transform: translateX(-50px);
          }
          100% {
            transform: translateX(-100%);
          }
        }

        .racelab-toast-enter {
          animation: racelab-toast-slideUp 1s cubic-bezier(0.25, 0.46, 0.45, 0.94);
        }

        .racelab-toast-marquee {
          animation: racelab-toast-marquee ${C}s linear infinite;
        }
      `}),e.jsxs("button",{onClick:N,className:p("left-4 right-4 flex flex-row items-center rounded-lg  ","bg-surface-contrast border-ui-brand-200 overflow-hidden  ","racelab-toast-enter",{"mx-auto mb-1 max-sm:w-full":o,"fixed bottom-16 z-50 border shadow-lg md:sticky md:bottom-4 md:mx-32":!o}),children:[e.jsxs("div",{className:"flex flex-row items-center justify-center gap-1 px-3 py-3",style:{background:"linear-gradient(64deg, var(--surface-contrast) -7.76%, var(--ui-brand-200) 90.05%)"},children:[e.jsx(M,{name:"racelab",size:"raw",className:"text-text-white"}),e.jsx("span",{className:"text-text-white text-sm font-bold",children:"LIVE"})]}),e.jsx("div",{ref:h,className:"flex-1 overflow-hidden px-3 py-1",children:e.jsx("div",{className:"flex flex-row items-center overflow-hidden",children:e.jsx("div",{ref:b,className:p("flex flex-row items-center gap-8 whitespace-nowrap",i&&"racelab-toast-marquee"),children:Array(i?2:1).fill(null).map((r,n)=>e.jsxs("div",{className:"flex flex-row items-center gap-2 px-8",children:[e.jsx("span",{className:"text-text-toast text-sm font-bold",children:t.title}),e.jsx("span",{className:"text-text-toast text-sm",children:t.description})]},n))})})})]})]})},z=({children:o,isFloating:l})=>{const t=j(u=>u.app.isMobileFooterVisible);return e.jsxs(e.Fragment,{children:[e.jsx("div",{className:"flex-1"}),e.jsxs("div",{className:p("fixed left-0 right-0 z-[50] md:sticky ",t?"bottom-14 md:bottom-0":"bottom-0",{"!sticky":l}),children:[e.jsx(D,{betbuilder:!0}),o]})]})};export{z as B,D as R,S as u};
