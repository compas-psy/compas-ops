/* @ds-bundle: {"format":4,"namespace":"DesignSystem_66188c","components":[{"name":"SERVICES","sourcePath":"components/brand/ServiceMark.jsx"},{"name":"ServiceMark","sourcePath":"components/brand/ServiceMark.jsx"},{"name":"Badge","sourcePath":"components/core/Badge.jsx"},{"name":"Button","sourcePath":"components/core/Button.jsx"},{"name":"Card","sourcePath":"components/core/Card.jsx"},{"name":"CardHeader","sourcePath":"components/core/Card.jsx"},{"name":"CardBody","sourcePath":"components/core/Card.jsx"},{"name":"Input","sourcePath":"components/core/Input.jsx"},{"name":"Separator","sourcePath":"components/core/Separator.jsx"},{"name":"ICON_NAMES","sourcePath":"components/icons/Icon.jsx"},{"name":"Icon","sourcePath":"components/icons/Icon.jsx"},{"name":"SegmentedControl","sourcePath":"components/navigation/SegmentedControl.jsx"}],"sourceHashes":{"components/brand/ServiceMark.jsx":"0867dd330755","components/core/Badge.jsx":"d2031d34cd26","components/core/Button.jsx":"2b273d273483","components/core/Card.jsx":"fb85a514c3f4","components/core/Input.jsx":"a8fae43048c8","components/core/Separator.jsx":"97d0efa1642d","components/icons/Icon.jsx":"3b323b6a1fc7","components/navigation/SegmentedControl.jsx":"4b641094c989","ui_kits/diary/DiaryApp.jsx":"6281121764a3","ui_kits/landing/LandingApp.jsx":"2284e1f89d04"},"inlinedExternals":[],"unexposedExports":[]} */

(() => {

const __ds_ns = (window.DesignSystem_66188c = window.DesignSystem_66188c || {});

const __ds_scope = {};

(__ds_ns.__errors = __ds_ns.__errors || []);

// components/brand/ServiceMark.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * СИМПАС ServiceMark — знак сервиса. Дерево одно и то же для всех продуктов;
 * различает сервисы ТОЛЬКО цветовая пара «фон + дерево». Контур знака не меняется.
 */
const TREE = "M271.601 95.5359C271.601 98.2689 259.391 156.577 256.531 167.5C249.23 195.393 238.289 218.802 227.876 228.815C221.636 234.814 219.516 234.178 214.819 224.91C205.691 206.897 201.739 175.513 205.247 148.917C207.074 135.071 213.077 115.363 219.069 103.538C222.349 97.066 222.482 96.2808 220.184 96.9449C218.749 97.3584 214.861 98.4778 211.543 99.4316C206.342 100.926 205.122 102.078 202.678 107.806C188.698 140.592 187.277 185.489 199.107 220.701C201.436 227.633 202.76 233.307 202.05 233.307C200.056 233.307 182.944 216.427 176.619 208.223C163.315 190.962 154.034 171.456 148.64 149.412L145.45 136.38L140.092 141.432C137.146 144.211 134.75 147.705 134.766 149.196C134.783 150.687 136.666 157.418 138.95 164.153C141.235 170.889 142.864 176.646 142.573 176.948C142.118 177.419 127.506 174.172 120.036 171.939C118.272 171.412 116.755 172.829 114.634 176.988L111.683 182.77L119.149 184.987C123.255 186.207 127.166 187.204 127.841 187.204C128.515 187.204 134.247 189.203 140.581 191.645C151.893 196.007 152.227 196.281 159.524 207.132C173.574 228.025 189.114 243.119 207.928 254.145C221.9 262.334 227.085 272.22 213.613 264.985C198.594 256.918 186.387 254.19 165.71 254.272C151.912 254.328 144.707 255.037 138.337 256.966C129.372 259.683 106.336 270.371 105.085 272.394C104.693 273.028 105.693 276.136 107.307 279.299L110.241 285.051L118.527 280.151C123.084 277.455 131.998 273.506 138.337 271.376C148.071 268.104 152.326 267.502 165.71 267.502C179.011 267.502 183.347 268.108 192.697 271.275C198.823 273.351 206.838 276.917 210.504 279.2L217.173 283.351L211.612 284.384C200.987 286.359 187.136 294.987 177.937 305.362C173.193 310.714 169.312 315.739 169.312 316.528C169.312 317.319 171.639 319.741 174.484 321.911L179.655 325.854L184.209 320.28C198.777 302.444 212.38 296.377 226.079 301.61C233.604 304.484 233.999 305.498 235.128 324.932C236.459 347.819 230.096 378.931 221.048 393.77C217.089 400.262 217.529 402.796 222.797 403.849C225.274 404.344 228.178 404.749 229.252 404.749C233.451 404.749 242.623 379.598 246.325 357.927C251.082 330.084 248.322 304.186 237.063 271.03L229.997 250.222L237.867 243.03C257.838 224.776 269.38 190.747 280.994 115.89C282.223 107.966 283.542 100.024 283.926 98.2416C284.567 95.255 284.109 95 278.111 95C274.531 95 271.601 95.2406 271.601 95.5359ZM233.97 107.152C225.525 112.892 222.496 124.626 226.788 134.979C231.571 146.513 247.109 134.708 247.109 119.541C247.109 112.533 243.712 103.644 241.034 103.644C239.989 103.644 236.81 105.223 233.97 107.152ZM316.838 111.928C317.339 114.108 318.152 122.441 318.643 130.448C320.692 163.857 310.685 196.961 288.204 231.146C264.737 266.83 255.753 294.551 255.753 331.274C255.753 355.735 258.506 370.114 267.705 393.709L272.108 405L278.697 404.02C282.322 403.48 285.463 402.871 285.676 402.665C285.891 402.459 283.795 397.494 281.017 391.633C274.03 376.885 269.994 360.511 269.16 343.52C268.458 329.209 268.484 329.067 273.047 322.265C283.95 306.013 307.906 290.103 327.949 285.806C340.298 283.158 354.595 283.24 366.863 286.03C372.424 287.295 377.178 288.106 377.427 287.831C377.678 287.557 378.329 284.621 378.877 281.308C379.868 275.303 379.853 275.277 374.359 273.593C364.478 270.564 335.328 270.132 323.33 272.836C309.482 275.957 291.921 284.536 280.605 293.709C275.653 297.723 271.601 300.506 271.601 299.893C271.601 296.452 279.883 274.189 284.81 264.384C295.847 242.422 313.133 222.052 332.322 208.387C344.619 199.632 366.049 189.512 378.931 186.379C384.478 185.03 389.614 183.385 390.345 182.724C391.14 182.005 390.572 179.475 388.936 176.435C386.396 171.721 385.825 171.417 381.123 172.267C366.459 174.921 347.028 183.012 329.437 193.79C323.956 197.147 323.262 197.292 323.958 194.94C330.082 174.224 332.681 154.025 331.707 134.731C331.264 125.953 330.513 117.799 330.039 116.61C329.248 114.624 319.119 107.966 316.888 107.966C316.36 107.966 316.337 109.748 316.838 111.928ZM288.965 123.454C286.895 130.909 287.095 133.703 290.191 140.519C292.959 146.614 300.661 152.628 305.699 152.628C310.176 152.628 311.94 149.65 311.94 142.091C311.94 132.491 307.413 124.67 299.694 120.933C291.602 117.015 290.694 117.228 288.965 123.454ZM168.759 139.726C163.266 146.254 161.475 153.566 163.512 161.134C165.163 167.264 170.55 174.238 173.634 174.238C176.684 174.238 182.098 167.293 183.707 161.317C185.654 154.087 184.553 148.13 179.914 140.781C175.755 134.193 173.587 133.988 168.759 139.726ZM353.1 141.281C348.496 144.901 343.714 154.38 343.666 159.977C343.62 165.365 346.36 172.43 348.869 173.393C351.547 174.42 357.761 171.276 361.875 166.811C369.472 158.566 368.739 139.453 360.789 138.512C358.687 138.264 355.457 139.427 353.1 141.281ZM280.35 175.153C274.113 183.081 277.667 200.643 286.612 206.098C292.414 209.636 292.478 209.622 295.974 203.963C301.668 194.751 299.299 180.985 290.884 174.365C285.876 170.426 283.954 170.57 280.35 175.153ZM226.157 176.885C219.425 181.048 216.454 186.67 216.284 195.569C216.088 205.823 218.278 207.432 227.583 203.879C237.105 200.241 241.66 193.084 241.067 182.689C240.627 174.984 240.609 174.957 235.584 174.567C232.555 174.33 228.789 175.257 226.157 176.885ZM364.143 205.209C361.555 206.334 359.038 208.295 358.55 209.567C355.879 216.527 367.209 224.663 379.571 224.663C385.472 224.663 387.648 223.913 391.349 220.607C395.298 217.079 395.671 216.149 394.226 213.45C393.312 211.742 389.764 208.705 386.342 206.698C379.299 202.571 371.427 202.044 364.143 205.209ZM126.109 207.815C123.089 208.849 120.868 210.662 120.542 212.36C119.773 216.374 124.106 223.72 128.923 226.564C136.842 231.242 156.346 228.362 156.346 222.514C156.346 219.271 147.523 210.034 142.368 207.88C136.77 205.542 132.802 205.526 126.109 207.815ZM324.759 230.814C318.632 234.907 314.827 242.31 314.824 250.138C314.821 254.623 315.465 256.361 317.342 256.937C322.183 258.421 331.089 255.169 336.212 250.046C340.654 245.603 341.357 243.904 341.91 236.262L342.54 227.544H336.097C331.797 227.544 328.026 228.632 324.759 230.814ZM140.663 282.001C138.003 283.427 134.233 287.295 132.284 290.596L128.74 296.598L133.539 299.524C143.475 305.582 157.416 302.101 163.557 292.028C167.392 285.739 167.189 284.196 162.181 281.607C156.463 278.65 146.572 278.831 140.663 282.001ZM343.895 292.573C339.437 293.858 339.18 294.261 340.106 298.566C342.705 310.637 350.124 316.867 361.898 316.867C365.813 316.867 370.174 316.02 371.588 314.985C374.014 313.212 374.026 312.778 371.789 307.422C368.595 299.779 362.895 294.1 356.683 292.374C350.753 290.727 350.275 290.734 343.895 292.573Z";
const SERVICES = {
  praktika: {
    ru: "ПРАКТИКА",
    sub: "кабинет специалиста",
    bg: "#1D4735",
    fg: "#F7F8F4"
  },
  momenty: {
    ru: "МОМЕНТЫ",
    sub: "медитации и практики",
    bg: "#4A4E78",
    fg: "#EDEBF2"
  },
  zapiski: {
    ru: "ЗАПИСКИ",
    sub: "заметки",
    bg: "#C8604A",
    fg: "#FBF3E3"
  },
  shagi: {
    ru: "ШАГИ",
    sub: "задачи",
    bg: "#3B8F5A",
    fg: "#F7F8F4"
  },
  stupeni: {
    ru: "СТУПЕНИ",
    sub: "повышение квалификации",
    bg: "#CC9E50",
    fg: "#143D2F"
  },
  grani: {
    ru: "ГРАНИ",
    sub: "диагностика, психоанкетирование",
    bg: "#5C2F42",
    fg: "#F5EAEF"
  },
  krugi: {
    ru: "КРУГИ",
    sub: "супервизия, интервизия, балинты",
    bg: "#6A5F63",
    fg: "#F7F3F1"
  },
  svoi: {
    ru: "СВОИ",
    sub: "сообщество",
    bg: "#A47864",
    fg: "#FAF5F0"
  },
  dnevnik: {
    ru: "ДНЕВНИК",
    sub: "для клиентов",
    bg: "#E7F0EA",
    fg: "#1D4735"
  }
};
function ServiceMark({
  service = "praktika",
  size = 64,
  shape = "squircle",
  bare = false,
  style,
  ...rest
}) {
  const s = SERVICES[service] || SERVICES.praktika;
  const tree = /*#__PURE__*/React.createElement("svg", {
    viewBox: "0 0 500 500",
    width: bare ? size : Math.round(size * 0.74),
    height: bare ? size : Math.round(size * 0.74),
    fill: "currentColor",
    style: {
      display: "block",
      flexShrink: 0
    }
  }, /*#__PURE__*/React.createElement("path", {
    fillRule: "evenodd",
    clipRule: "evenodd",
    d: TREE
  }));
  if (bare) {
    return /*#__PURE__*/React.createElement("span", _extends({
      style: {
        display: "inline-flex",
        color: s.bg,
        ...style
      }
    }, rest), tree);
  }
  const radius = shape === "circle" ? "50%" : shape === "rounded" ? size * 0.18 : size * 0.28;
  return /*#__PURE__*/React.createElement("span", _extends({
    style: {
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      width: size,
      height: size,
      flexShrink: 0,
      background: s.bg,
      color: s.fg,
      borderRadius: radius,
      ...style
    }
  }, rest), tree);
}
Object.assign(__ds_scope, { SERVICES, ServiceMark });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/brand/ServiceMark.jsx", error: String((e && e.message) || e) }); }

// components/core/Badge.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * СИМПАС Badge / status pill. Small, pill-shaped, soft-tinted. Used for session
 * statuses, payment states, and "new" flags across the dashboard.
 */
const VARIANTS = {
  default: {
    bg: "var(--forest-800)",
    fg: "#fff",
    bd: "transparent"
  },
  secondary: {
    bg: "var(--muted)",
    fg: "var(--foreground)",
    bd: "transparent"
  },
  outline: {
    bg: "transparent",
    fg: "var(--foreground)",
    bd: "var(--border)"
  },
  // soft-tint status pairs
  confirmed: {
    bg: "var(--success-soft)",
    fg: "var(--success-500)",
    bd: "color-mix(in srgb, var(--success-500) 20%, transparent)"
  },
  pending: {
    bg: "var(--orange-soft)",
    fg: "var(--orange-500)",
    bd: "color-mix(in srgb, var(--orange-500) 20%, transparent)"
  },
  completed: {
    bg: "var(--sage-100)",
    fg: "var(--muted-foreground)",
    bd: "var(--border)"
  },
  cancelled: {
    bg: "var(--red-soft)",
    fg: "var(--red-500)",
    bd: "color-mix(in srgb, var(--red-500) 20%, transparent)"
  },
  paid: {
    bg: "color-mix(in srgb, var(--forest-800) 10%, transparent)",
    fg: "var(--forest-800)",
    bd: "color-mix(in srgb, var(--forest-800) 20%, transparent)"
  },
  processing: {
    bg: "var(--blue-soft)",
    fg: "var(--blue-500)",
    bd: "color-mix(in srgb, var(--blue-500) 20%, transparent)"
  },
  new: {
    bg: "var(--amber-soft)",
    fg: "var(--gold-500)",
    bd: "color-mix(in srgb, var(--gold-500) 25%, transparent)"
  }
};
function Badge({
  children,
  variant = "default",
  dot = false,
  style,
  ...rest
}) {
  const v = VARIANTS[variant] || VARIANTS.default;
  return /*#__PURE__*/React.createElement("span", _extends({
    style: {
      display: "inline-flex",
      alignItems: "center",
      gap: 5,
      padding: "3px 10px",
      borderRadius: "var(--radius-full)",
      fontFamily: "var(--font-sans)",
      fontSize: 12,
      fontWeight: 600,
      lineHeight: 1.3,
      background: v.bg,
      color: v.fg,
      border: `1px solid ${v.bd}`,
      whiteSpace: "nowrap",
      ...style
    }
  }, rest), dot && /*#__PURE__*/React.createElement("span", {
    style: {
      width: 6,
      height: 6,
      borderRadius: "50%",
      background: "currentColor"
    }
  }), children);
}
Object.assign(__ds_scope, { Badge });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Badge.jsx", error: String((e && e.message) || e) }); }

// components/core/Button.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * СИМПАС Button — the product's core action control.
 * Forest-green primary, gold accent for premium/important actions, calm outline
 * secondary. iOS press feel (active:scale .97), soft rounding, low shadow.
 */
const RADIUS = "14px";
const SIZES = {
  sm: {
    height: 36,
    padding: "0 14px",
    font: 13,
    gap: 6
  },
  md: {
    height: 44,
    padding: "0 20px",
    font: 14,
    gap: 8
  },
  lg: {
    height: 52,
    padding: "0 28px",
    font: 15,
    gap: 8
  }
};
const VARIANTS = {
  primary: {
    background: "var(--forest-800)",
    color: "#fff",
    border: "1px solid transparent",
    boxShadow: "var(--shadow-sm)",
    hoverBg: "var(--forest-700)"
  },
  accent: {
    background: "var(--gold-500)",
    color: "var(--forest-900)",
    border: "1px solid transparent",
    boxShadow: "var(--shadow-sm)",
    hoverBg: "var(--gold-400)"
  },
  secondary: {
    background: "var(--card)",
    color: "var(--forest-800)",
    border: "1px solid var(--border)",
    boxShadow: "none",
    hoverBg: "var(--sage-50)"
  },
  ghost: {
    background: "transparent",
    color: "var(--forest-800)",
    border: "1px solid transparent",
    boxShadow: "none",
    hoverBg: "var(--sage-100)"
  },
  destructive: {
    background: "var(--destructive)",
    color: "#fff",
    border: "1px solid transparent",
    boxShadow: "var(--shadow-sm)",
    hoverBg: "#c94f43"
  }
};
function Button({
  children,
  variant = "primary",
  size = "md",
  block = false,
  disabled = false,
  leadingIcon,
  trailingIcon,
  style,
  ...rest
}) {
  const [hover, setHover] = React.useState(false);
  const [press, setPress] = React.useState(false);
  const v = VARIANTS[variant] || VARIANTS.primary;
  const s = SIZES[size] || SIZES.md;
  return /*#__PURE__*/React.createElement("button", _extends({
    disabled: disabled,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => {
      setHover(false);
      setPress(false);
    },
    onMouseDown: () => setPress(true),
    onMouseUp: () => setPress(false),
    style: {
      display: block ? "flex" : "inline-flex",
      width: block ? "100%" : undefined,
      alignItems: "center",
      justifyContent: "center",
      gap: s.gap,
      height: s.height,
      padding: s.padding,
      fontFamily: "var(--font-sans)",
      fontSize: s.font,
      fontWeight: 600,
      lineHeight: 1,
      borderRadius: RADIUS,
      cursor: disabled ? "not-allowed" : "pointer",
      opacity: disabled ? 0.5 : 1,
      background: hover && !disabled ? v.hoverBg : v.background,
      color: v.color,
      border: v.border,
      boxShadow: v.boxShadow,
      transform: press && !disabled ? "scale(0.97)" : "scale(1)",
      transition: "background .15s ease, transform .1s ease, box-shadow .15s ease",
      whiteSpace: "nowrap",
      ...style
    }
  }, rest), leadingIcon, children, trailingIcon);
}
Object.assign(__ds_scope, { Button });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Button.jsx", error: String((e && e.message) || e) }); }

// components/core/Card.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * СИМПАС Card — white surface, hairline border, soft floating shadow, generous
 * rounding. The default container for every dashboard panel.
 */
function Card({
  children,
  interactive = false,
  padding = 0,
  style,
  ...rest
}) {
  const [hover, setHover] = React.useState(false);
  return /*#__PURE__*/React.createElement("div", _extends({
    onMouseEnter: () => interactive && setHover(true),
    onMouseLeave: () => interactive && setHover(false),
    style: {
      background: "var(--card)",
      border: "1px solid var(--border)",
      borderRadius: "var(--radius-xl)",
      boxShadow: hover ? "var(--shadow-card-hover)" : "var(--shadow-card)",
      color: "var(--card-foreground)",
      padding: padding || undefined,
      transition: "box-shadow .3s ease",
      overflow: "hidden",
      ...style
    }
  }, rest), children);
}

/** Card header row with title + optional trailing action/icon. */
function CardHeader({
  title,
  icon,
  action,
  style,
  ...rest
}) {
  return /*#__PURE__*/React.createElement("div", _extends({
    style: {
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      padding: "14px 20px",
      borderBottom: "1px solid var(--border)",
      ...style
    }
  }, rest), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 10
    }
  }, icon, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 14,
      fontWeight: 700,
      color: "var(--foreground)"
    }
  }, title)), action);
}

/** Padded card body. */
function CardBody({
  children,
  style,
  ...rest
}) {
  return /*#__PURE__*/React.createElement("div", _extends({
    style: {
      padding: 20,
      ...style
    }
  }, rest), children);
}
Object.assign(__ds_scope, { Card, CardHeader, CardBody });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Card.jsx", error: String((e && e.message) || e) }); }

// components/core/Input.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * СИМПАС Input — white surface, medium rounding, gold focus ring. Matches the
 * auth/form fields. Optional leading/trailing adornments.
 */
function Input({
  leading,
  trailing,
  style,
  wrapperStyle,
  ...rest
}) {
  const [focus, setFocus] = React.useState(false);
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 8,
      background: "var(--input)",
      border: `1px solid ${focus ? "var(--ring)" : "var(--border)"}`,
      borderRadius: "var(--radius-md)",
      padding: "0 14px",
      boxShadow: focus ? "0 0 0 3px color-mix(in srgb, var(--ring) 22%, transparent)" : "none",
      transition: "border-color .15s ease, box-shadow .15s ease",
      ...wrapperStyle
    }
  }, leading && /*#__PURE__*/React.createElement("span", {
    style: {
      color: "var(--muted-foreground)",
      display: "flex"
    }
  }, leading), /*#__PURE__*/React.createElement("input", _extends({
    onFocus: e => {
      setFocus(true);
      rest.onFocus && rest.onFocus(e);
    },
    onBlur: e => {
      setFocus(false);
      rest.onBlur && rest.onBlur(e);
    },
    style: {
      flex: 1,
      border: "none",
      outline: "none",
      background: "transparent",
      fontFamily: "var(--font-sans)",
      fontSize: 15,
      fontWeight: 500,
      color: "var(--foreground)",
      height: 46,
      minWidth: 0,
      ...style
    }
  }, rest)), trailing && /*#__PURE__*/React.createElement("span", {
    style: {
      color: "var(--muted-foreground)",
      display: "flex"
    }
  }, trailing));
}
Object.assign(__ds_scope, { Input });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Input.jsx", error: String((e && e.message) || e) }); }

// components/core/Separator.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/** СИМПАС Separator — hairline divider in the brand border color. */
function Separator({
  orientation = "horizontal",
  style,
  ...rest
}) {
  return /*#__PURE__*/React.createElement("div", _extends({
    role: "separator",
    "aria-orientation": orientation,
    style: {
      background: "var(--border)",
      flexShrink: 0,
      ...(orientation === "horizontal" ? {
        height: 1,
        width: "100%"
      } : {
        width: 1,
        height: "100%"
      }),
      ...style
    }
  }, rest));
}
Object.assign(__ds_scope, { Separator });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Separator.jsx", error: String((e && e.message) || e) }); }

// components/icons/Icon.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * СИМПАС Icon — the product's custom stroke icon set (ported verbatim from the
 * app's note-icons.tsx). Monochrome, currentColor, viewBox 24, strokeWidth 1.75,
 * round caps/joins. Use for note blocks and toolbar actions; for generic glyphs
 * the app pairs this with Lucide (link lucide from CDN in a consuming page).
 */
const P = {
  // ── Note block icons ──
  request: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("circle", {
    cx: "12",
    cy: "12",
    r: "9"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "12",
    cy: "12",
    r: "4"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "12",
    y1: "2",
    x2: "12",
    y2: "5"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "12",
    y1: "19",
    x2: "12",
    y2: "22"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "2",
    y1: "12",
    x2: "5",
    y2: "12"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "19",
    y1: "12",
    x2: "22",
    y2: "12"
  })),
  anamnesis: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("path", {
    d: "M9 3h6v2a1 1 0 0 1-1 1h-4a1 1 0 0 1-1-1V3Z"
  }), /*#__PURE__*/React.createElement("rect", {
    x: "5",
    y: "5",
    width: "14",
    height: "16",
    rx: "2"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "9",
    y1: "10",
    x2: "15",
    y2: "10"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "9",
    y1: "13",
    x2: "15",
    y2: "13"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "9",
    y1: "16",
    x2: "12",
    y2: "16"
  })),
  observation: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("path", {
    d: "M2.5 12C4 7.5 7.5 5 12 5s8 2.5 9.5 7c-1.5 4.5-5 7-9.5 7s-8-2.5-9.5-7Z"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "12",
    cy: "12",
    r: "3"
  })),
  intervention: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("path", {
    d: "m15 4 5 5-11 11H4v-5L15 4Z"
  }), /*#__PURE__*/React.createElement("path", {
    d: "m13.5 6.5 4 4"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M20 7 17 4"
  })),
  resources: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("path", {
    d: "M4 4.5C4 3.67 4.67 3 5.5 3H9c1.1 0 2 .5 3 1.5 1-.5 1.9-1.5 3-1.5h3.5c.83 0 1.5.67 1.5 1.5v13c0 .83-.67 1.5-1.5 1.5H15c-1.1 0-2 .5-3 1.5-1-1-1.9-1.5-3-1.5H5.5C4.67 19 4 18.33 4 17.5v-13Z"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M12 4.5V20"
  })),
  dynamics: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("polyline", {
    points: "4 18 9 12 13 15 20 6"
  }), /*#__PURE__*/React.createElement("polyline", {
    points: "16 6 20 6 20 10"
  })),
  homework: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("path", {
    d: "M14 3v4a1 1 0 0 0 1 1h4"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M17 21H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7l5 5v11a2 2 0 0 1-2 2Z"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "9",
    y1: "11",
    x2: "15",
    y2: "11"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "9",
    y1: "14",
    x2: "15",
    y2: "14"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "9",
    y1: "17",
    x2: "12",
    y2: "17"
  })),
  next_step: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("circle", {
    cx: "12",
    cy: "12",
    r: "9"
  }), /*#__PURE__*/React.createElement("polyline", {
    points: "10 8 14 12 10 16"
  })),
  private: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("rect", {
    x: "5",
    y: "11",
    width: "14",
    height: "10",
    rx: "2"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M8 11V7a4 4 0 0 1 8 0v4"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "12",
    cy: "16",
    r: "1"
  })),
  for_client: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("circle", {
    cx: "10",
    cy: "8",
    r: "4"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M10 14c-4 0-7 2-7 4v1h10"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "19",
    cy: "16",
    r: "2"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M19 14v-1"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M19 18v1"
  })),
  quote: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("path", {
    d: "M10 8H6a2 2 0 0 0-2 2v2a2 2 0 0 0 2 2h1l-1 4"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M20 8h-4a2 2 0 0 0-2 2v2a2 2 0 0 0 2 2h1l-1 4"
  })),
  hypothesis: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("path", {
    d: "M9 18h6"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M10 21h4"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M12 3a6 6 0 0 1 4 10.5V16H8v-2.5A6 6 0 0 1 12 3Z"
  })),
  // ── Action icons ──
  search: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("circle", {
    cx: "10.5",
    cy: "10.5",
    r: "6.5"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "15.5",
    y1: "15.5",
    x2: "21",
    y2: "21"
  })),
  add: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("circle", {
    cx: "12",
    cy: "12",
    r: "9"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "12",
    y1: "8",
    x2: "12",
    y2: "16"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "8",
    y1: "12",
    x2: "16",
    y2: "12"
  })),
  filter: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("polygon", {
    points: "3 4 21 4 14 12.5 14 18 10 20 10 12.5 3 4"
  })),
  import: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("path", {
    d: "M12 3v12"
  }), /*#__PURE__*/React.createElement("polyline", {
    points: "8 11 12 15 16 11"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M4 19h16"
  })),
  export: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("path", {
    d: "M12 15V3"
  }), /*#__PURE__*/React.createElement("polyline", {
    points: "8 7 12 3 16 7"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M4 19h16"
  })),
  share: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("circle", {
    cx: "6",
    cy: "12",
    r: "2.5"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "18",
    cy: "6",
    r: "2.5"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "18",
    cy: "18",
    r: "2.5"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "8.3",
    y1: "10.9",
    x2: "15.7",
    y2: "7.1"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "8.3",
    y1: "13.1",
    x2: "15.7",
    y2: "16.9"
  })),
  archive: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("rect", {
    x: "2",
    y: "3",
    width: "20",
    height: "5",
    rx: "1"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M4 8v10a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "10",
    y1: "13",
    x2: "14",
    y2: "13"
  })),
  delete: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("path", {
    d: "M4 6h16"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M6 6v12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V6"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M9 3h6"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "10",
    y1: "10",
    x2: "10",
    y2: "16"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "14",
    y1: "10",
    x2: "14",
    y2: "16"
  })),
  save: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("path", {
    d: "M5 3h11l5 5v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z"
  }), /*#__PURE__*/React.createElement("polyline", {
    points: "14 3 14 8 8 8"
  }), /*#__PURE__*/React.createElement("rect", {
    x: "7",
    y: "14",
    width: "10",
    height: "6",
    rx: "1"
  })),
  close: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("line", {
    x1: "6",
    y1: "6",
    x2: "18",
    y2: "18"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "18",
    y1: "6",
    x2: "6",
    y2: "18"
  })),
  back: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("polyline", {
    points: "14 18 8 12 14 6"
  })),
  mic: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("rect", {
    x: "9",
    y: "2",
    width: "6",
    height: "12",
    rx: "3"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M5 10a7 7 0 0 0 14 0"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "12",
    y1: "17",
    x2: "12",
    y2: "21"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "8",
    y1: "21",
    x2: "16",
    y2: "21"
  })),
  attach: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("path", {
    d: "M15.5 3.5a3.5 3.5 0 0 1 0 5l-9 9a2.5 2.5 0 0 1-3.5-3.5l9-9a1.5 1.5 0 0 1 2 2l-7 7"
  })),
  tags: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("path", {
    d: "M3 6.5V3h3.5L20 16.5 16.5 20 3 6.5Z"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "7",
    cy: "7",
    r: "1.5"
  })),
  more: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("circle", {
    cx: "12",
    cy: "5",
    r: "1",
    fill: "currentColor",
    stroke: "none"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "12",
    cy: "12",
    r: "1",
    fill: "currentColor",
    stroke: "none"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "12",
    cy: "19",
    r: "1",
    fill: "currentColor",
    stroke: "none"
  })),
  sync: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("polyline", {
    points: "4 10 1 7 4 4"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M1 7h15a5 5 0 0 1 5 5"
  }), /*#__PURE__*/React.createElement("polyline", {
    points: "20 14 23 17 20 20"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M23 17H8a5 5 0 0 1-5-5"
  })),
  meeting: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("rect", {
    x: "2",
    y: "4",
    width: "20",
    height: "14",
    rx: "2"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M10 9l4 3-4 3V9Z"
  })),
  payment: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("rect", {
    x: "2",
    y: "5",
    width: "20",
    height: "14",
    rx: "2"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "2",
    y1: "10",
    x2: "22",
    y2: "10"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M9 15h2"
  })),
  format: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("path", {
    d: "M6 18 10 6h4l4 12"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M7.5 14h9"
  })),
  list: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("line", {
    x1: "9",
    y1: "6",
    x2: "20",
    y2: "6"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "9",
    y1: "12",
    x2: "20",
    y2: "12"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "9",
    y1: "18",
    x2: "20",
    y2: "18"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "5",
    cy: "6",
    r: "1",
    fill: "currentColor",
    stroke: "none"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "5",
    cy: "12",
    r: "1",
    fill: "currentColor",
    stroke: "none"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "5",
    cy: "18",
    r: "1",
    fill: "currentColor",
    stroke: "none"
  }))
};
const ICON_NAMES = Object.keys(P);
function Icon({
  name,
  size = 20,
  strokeWidth = 1.75,
  style,
  ...rest
}) {
  const paths = P[name];
  return /*#__PURE__*/React.createElement("svg", _extends({
    xmlns: "http://www.w3.org/2000/svg",
    viewBox: "0 0 24 24",
    width: size,
    height: size,
    fill: "none",
    stroke: "currentColor",
    strokeWidth: strokeWidth,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    style: {
      flexShrink: 0,
      display: "block",
      ...style
    }
  }, rest), paths || null);
}
Object.assign(__ds_scope, { ICON_NAMES, Icon });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/icons/Icon.jsx", error: String((e && e.message) || e) }); }

// components/navigation/SegmentedControl.jsx
try { (() => {
/**
 * СИМПАС SegmentedControl — the app's iOS-style pill toggle (see .note-segmented).
 * Used for private/client note modes and small view switches. Active segment gets
 * a white raised chip; supports per-item accent (forest vs gold) via `accent`.
 */
function SegmentedControl({
  options = [],
  value,
  onChange,
  accent = "forest",
  style
}) {
  const activeColor = accent === "gold" ? "var(--gold-500)" : "var(--forest-800)";
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 2,
      padding: 3,
      background: "var(--muted)",
      borderRadius: "var(--radius-md)",
      ...style
    }
  }, options.map(opt => {
    const val = typeof opt === "string" ? opt : opt.value;
    const label = typeof opt === "string" ? opt : opt.label;
    const icon = typeof opt === "string" ? null : opt.icon;
    const active = val === value;
    return /*#__PURE__*/React.createElement("button", {
      key: val,
      onClick: () => onChange && onChange(val),
      style: {
        flex: 1,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 6,
        padding: "10px 12px",
        borderRadius: 11,
        border: "none",
        cursor: "pointer",
        fontFamily: "var(--font-sans)",
        fontSize: 14,
        fontWeight: 600,
        background: active ? "var(--card)" : "transparent",
        color: active ? activeColor : "var(--muted-foreground)",
        boxShadow: active ? "0 1px 3px rgba(20,32,24,0.08)" : "none",
        transition: "all .2s ease"
      }
    }, icon, label);
  }));
}
Object.assign(__ds_scope, { SegmentedControl });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/navigation/SegmentedControl.jsx", error: String((e && e.message) || e) }); }

// ui_kits/diary/DiaryApp.jsx
try { (() => {
/* СИМПАС — Diary dashboard recreation. Composes DS components (Card, Badge,
 * Button, Icon, SegmentedControl) into the psychologist's "Сегодня" workspace.
 * Exports window.DiaryApp. */
const {
  Card,
  CardHeader,
  CardBody,
  Badge,
  Button,
  Icon,
  SegmentedControl,
  Separator
} = window.DesignSystem_66188c;
const NAV = [{
  icon: "list",
  label: "Сегодня",
  active: true
}, {
  icon: "anamnesis",
  label: "Календарь"
}, {
  icon: "sync",
  label: "Расписание"
}, {
  icon: "for_client",
  label: "Клиенты"
}, {
  icon: "format",
  label: "Заметки"
}, {
  icon: "meeting",
  label: "Уведомления"
}, {
  icon: "save",
  label: "Документы"
}];
const WEEK = [{
  d: "Пн",
  n: 12,
  c: 4
}, {
  d: "Вт",
  n: 13,
  c: 3
}, {
  d: "Ср",
  n: 14,
  c: 5
}, {
  d: "Чт",
  n: 15,
  c: 3,
  today: true
}, {
  d: "Пт",
  n: 16,
  c: 2
}, {
  d: "Сб",
  n: 17,
  c: 0
}, {
  d: "Вс",
  n: 18,
  c: 0
}];
function Avatar({
  name,
  size = 36,
  bg = "var(--sage-100)",
  fg = "var(--forest-700)"
}) {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      width: size,
      height: size,
      borderRadius: "50%",
      background: bg,
      color: fg,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      fontWeight: 700,
      fontSize: size * 0.32,
      textTransform: "uppercase",
      flexShrink: 0,
      border: "1px solid var(--sage-200)"
    }
  }, name.slice(0, 2));
}
function Sidebar() {
  return /*#__PURE__*/React.createElement("aside", {
    style: {
      width: 232,
      background: "var(--sidebar)",
      padding: "22px 14px",
      display: "flex",
      flexDirection: "column",
      gap: 4,
      flexShrink: 0,
      minHeight: "100%"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 10,
      padding: "4px 10px 22px"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: 30,
      height: 30,
      borderRadius: 9,
      background: "rgba(255,255,255,.14)",
      display: "flex",
      alignItems: "center",
      justifyContent: "center"
    }
  }, /*#__PURE__*/React.createElement("img", {
    src: "../../assets/logo.svg",
    alt: "",
    style: {
      width: 22,
      height: 22
    }
  })), /*#__PURE__*/React.createElement("span", {
    style: {
      color: "rgba(255,255,255,.92)",
      fontSize: 14,
      fontWeight: 800,
      letterSpacing: "0.14em"
    }
  }, "\u0421\u0418\u041C\u041F\u0410\u0421")), NAV.map(it => /*#__PURE__*/React.createElement("div", {
    key: it.label,
    style: {
      display: "flex",
      alignItems: "center",
      gap: 11,
      padding: "10px 12px",
      borderRadius: 12,
      fontSize: 13.5,
      fontWeight: it.active ? 600 : 500,
      color: it.active ? "#fff" : "rgba(255,255,255,.6)",
      background: it.active ? "var(--sidebar-active)" : "transparent",
      cursor: "pointer"
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: it.icon,
    size: 18,
    strokeWidth: it.active ? 2 : 1.6
  }), /*#__PURE__*/React.createElement("span", null, it.label))), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: "auto",
      display: "flex",
      alignItems: "center",
      gap: 10,
      padding: "10px 8px",
      borderTop: "1px solid var(--sidebar-border)",
      marginTop: "auto"
    }
  }, /*#__PURE__*/React.createElement(Avatar, {
    name: "\u041C\u0421",
    size: 32,
    bg: "rgba(255,255,255,.14)",
    fg: "#fff"
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      color: "#fff",
      fontSize: 12.5,
      fontWeight: 600
    }
  }, "\u041C\u0430\u0440\u0438\u044F \u0421."), /*#__PURE__*/React.createElement("div", {
    style: {
      color: "rgba(255,255,255,.5)",
      fontSize: 11
    }
  }, "\u041F\u0441\u0438\u0445\u043E\u043B\u043E\u0433"))));
}
function WeekStrip() {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      background: "var(--card)",
      borderRadius: 18,
      border: "1px solid var(--border)",
      boxShadow: "var(--shadow-card)",
      overflow: "hidden"
    }
  }, WEEK.map(w => /*#__PURE__*/React.createElement("div", {
    key: w.n,
    style: {
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      padding: "10px 15px",
      background: w.today ? "var(--forest-800)" : "transparent",
      borderRadius: w.today ? 16 : 0
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 10,
      fontWeight: 600,
      textTransform: "uppercase",
      color: w.today ? "rgba(255,255,255,.7)" : "var(--muted-foreground)"
    }
  }, w.d), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 15,
      fontWeight: 700,
      marginTop: 2,
      color: w.today ? "#fff" : "var(--foreground)"
    }
  }, w.n), w.c > 0 && /*#__PURE__*/React.createElement("span", {
    style: {
      width: 5,
      height: 5,
      borderRadius: "50%",
      marginTop: 5,
      background: w.today ? "rgba(255,255,255,.6)" : "var(--forest-500)"
    }
  }))));
}
function NextSession() {
  return /*#__PURE__*/React.createElement(Card, null, /*#__PURE__*/React.createElement("div", {
    style: {
      padding: "16px 20px 0",
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between"
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "text-caption",
    style: {
      color: "var(--forest-600)"
    }
  }, "\u0421\u043B\u0435\u0434\u0443\u044E\u0449\u0430\u044F \u0441\u0435\u0441\u0441\u0438\u044F"), /*#__PURE__*/React.createElement(Badge, {
    variant: "confirmed",
    dot: true
  }, "\u0447\u0435\u0440\u0435\u0437 2 \u0447")), /*#__PURE__*/React.createElement(CardBody, {
    style: {
      padding: "12px 20px 20px"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "flex-start",
      gap: 16
    }
  }, /*#__PURE__*/React.createElement(Avatar, {
    name: "\u041C\u0430\u0440\u0438\u044F \u0421\u043C\u0438\u0440\u043D\u043E\u0432\u0430",
    size: 56
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("h3", {
    style: {
      margin: 0,
      fontSize: 20,
      fontWeight: 700
    }
  }, "\u041C\u0430\u0440\u0438\u044F \u0421\u043C\u0438\u0440\u043D\u043E\u0432\u0430"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 6,
      fontSize: 12,
      color: "var(--muted-foreground)",
      fontWeight: 600,
      margin: "3px 0 4px"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      width: 7,
      height: 7,
      borderRadius: "50%",
      background: "var(--success-500)"
    }
  }), " 8 \u0441\u0435\u0441\u0441\u0438\u0439"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: "var(--muted-foreground)"
    }
  }, "\u0417\u0430\u043F\u0440\u043E\u0441: \u0442\u0440\u0435\u0432\u043E\u0433\u0430, \u0441\u043E\u043D \xB7 \u0418\u043D\u0434\u0438\u0432\u0438\u0434\u0443\u0430\u043B\u044C\u043D\u0430\u044F")), /*#__PURE__*/React.createElement("div", {
    style: {
      textAlign: "right"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 20,
      fontWeight: 700,
      fontVariantNumeric: "tabular-nums"
    }
  }, "19:00 \u2013 20:00"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 4,
      justifyContent: "flex-end",
      fontSize: 12,
      color: "var(--muted-foreground)",
      marginTop: 3
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "meeting",
    size: 14
  }), " \u041E\u043D\u043B\u0430\u0439\u043D"))), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 12,
      padding: 12,
      background: "var(--sage-50)",
      borderRadius: 14,
      border: "1px solid var(--sage-200)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "text-caption",
    style: {
      color: "var(--forest-600)",
      display: "flex",
      alignItems: "center",
      gap: 6,
      marginBottom: 6
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "dynamics",
    size: 13
  }), " \u041A\u0440\u0430\u0442\u043A\u043E \u043E \u043F\u0440\u043E\u0448\u043B\u043E\u043C \u0441\u0435\u0430\u043D\u0441\u0435"), /*#__PURE__*/React.createElement("p", {
    style: {
      margin: 0,
      fontSize: 12.5,
      color: "var(--muted-foreground)",
      lineHeight: 1.5
    }
  }, "\uD83D\uDCC8 \u0414\u0438\u043D\u0430\u043C\u0438\u043A\u0430: \u0441\u0442\u0430\u043B\u043E \u043B\u0435\u0433\u0447\u0435 \u0437\u0430\u0441\u044B\u043F\u0430\u0442\u044C. \u27A1\uFE0F \u041F\u043B\u0430\u043D: \u0437\u0430\u043A\u0440\u0435\u043F\u0438\u0442\u044C \u0434\u044B\u0445\u0430\u0442\u0435\u043B\u044C\u043D\u0443\u044E \u043F\u0440\u0430\u043A\u0442\u0438\u043A\u0443, \u043E\u0431\u0441\u0443\u0434\u0438\u0442\u044C \u0433\u0440\u0430\u043D\u0438\u0446\u044B \u043D\u0430 \u0440\u0430\u0431\u043E\u0442\u0435.")), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 8,
      marginTop: 16
    }
  }, /*#__PURE__*/React.createElement(Button, {
    variant: "primary",
    size: "sm",
    block: true,
    leadingIcon: /*#__PURE__*/React.createElement(Icon, {
      name: "anamnesis",
      size: 16
    })
  }, "\u041F\u043E\u0434\u0433\u043E\u0442\u043E\u0432\u0438\u0442\u044C\u0441\u044F"), /*#__PURE__*/React.createElement(Button, {
    variant: "secondary",
    size: "sm",
    leadingIcon: /*#__PURE__*/React.createElement(Icon, {
      name: "meeting",
      size: 16
    })
  }, "\u041E\u0442\u043A\u0440\u044B\u0442\u044C Zoom"))));
}
const SCHEDULE = [{
  time: "09:00",
  end: "09:50",
  name: "Анна П.",
  fmt: "В кабинете",
  dur: 50,
  status: "completed"
}, {
  time: "11:00",
  end: "11:50",
  name: "Алексей В.",
  fmt: "Онлайн",
  dur: 50,
  status: "completed"
}, {
  time: "15:00",
  end: "15:50",
  name: "Ирина К.",
  fmt: "Онлайн",
  dur: 50,
  status: "pending"
}, {
  time: "19:00",
  end: "20:00",
  name: "Мария Смирнова",
  fmt: "Онлайн",
  dur: 60,
  status: "confirmed",
  next: true
}];
const STATUS_LABEL = {
  completed: "Завершено",
  pending: "Ожидает",
  confirmed: "Подтверждено",
  cancelled: "Отменено"
};
function Schedule() {
  const [filter, setFilter] = React.useState("all");
  const rows = filter === "all" ? SCHEDULE : SCHEDULE.filter(s => s.status === filter);
  return /*#__PURE__*/React.createElement(Card, null, /*#__PURE__*/React.createElement(CardHeader, {
    title: "\u0420\u0430\u0441\u043F\u0438\u0441\u0430\u043D\u0438\u0435 \u043D\u0430 \u0441\u0435\u0433\u043E\u0434\u043D\u044F",
    icon: /*#__PURE__*/React.createElement(Icon, {
      name: "anamnesis",
      size: 16,
      style: {
        color: "var(--forest-600)"
      }
    }),
    action: /*#__PURE__*/React.createElement("div", {
      style: {
        width: 210
      }
    }, /*#__PURE__*/React.createElement(SegmentedControl, {
      value: filter,
      onChange: setFilter,
      options: [{
        value: "all",
        label: "Все"
      }, {
        value: "confirmed",
        label: "Активные"
      }, {
        value: "completed",
        label: "Завершено"
      }]
    }))
  }), /*#__PURE__*/React.createElement("div", null, rows.map((s, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    style: {
      display: "flex",
      gap: 12,
      padding: "12px 20px",
      alignItems: "center",
      borderTop: i ? "1px solid color-mix(in srgb, var(--border) 50%, transparent)" : "none",
      background: s.next ? "var(--sage-50)" : "transparent",
      opacity: s.status === "completed" ? 0.55 : 1
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: 46,
      textAlign: "right",
      flexShrink: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 13,
      fontWeight: 700,
      fontVariantNumeric: "tabular-nums",
      color: s.next ? "var(--forest-800)" : "var(--foreground)"
    }
  }, s.time), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 10,
      color: "var(--muted-foreground)"
    }
  }, s.end)), /*#__PURE__*/React.createElement("span", {
    style: {
      width: 10,
      height: 10,
      borderRadius: "50%",
      flexShrink: 0,
      background: s.next ? "var(--forest-800)" : s.status === "completed" ? "var(--sage-300)" : s.status === "pending" ? "var(--orange-500)" : "var(--forest-500)"
    }
  }), /*#__PURE__*/React.createElement(Avatar, {
    name: s.name,
    size: 34
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 13,
      fontWeight: 700,
      textDecoration: s.status === "completed" ? "line-through" : "none",
      color: s.status === "completed" ? "var(--muted-foreground)" : "var(--foreground)"
    }
  }, s.name), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: "var(--muted-foreground)",
      display: "flex",
      gap: 5,
      alignItems: "center",
      marginTop: 1
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "meeting",
    size: 12
  }), " ", s.fmt, " \xB7 ", s.dur, " \u043C\u0438\u043D")), /*#__PURE__*/React.createElement(Badge, {
    variant: s.status
  }, STATUS_LABEL[s.status]))), /*#__PURE__*/React.createElement("button", {
    onClick: () => {},
    style: {
      width: "100%",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      gap: 8,
      padding: "13px",
      fontSize: 13,
      fontWeight: 600,
      color: "var(--forest-600)",
      background: "transparent",
      border: "none",
      borderTop: "1px solid var(--border)",
      cursor: "pointer",
      fontFamily: "var(--font-sans)"
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "add",
    size: 16
  }), " \u0414\u043E\u0431\u0430\u0432\u0438\u0442\u044C \u0437\u0430\u043F\u0438\u0441\u044C")));
}
function Attention() {
  const items = [{
    icon: "private",
    tint: "var(--red-soft)",
    ic: "var(--red-500)",
    t: "Нет согласия на обработку данных",
    s: "2 клиента",
    a: "Открыть"
  }, {
    icon: "homework",
    tint: "var(--amber-soft)",
    ic: "var(--gold-500)",
    t: "Домашние задания не заполнены",
    s: "3 записи",
    a: "Проверить"
  }, {
    icon: "payment",
    tint: "var(--orange-soft)",
    ic: "var(--orange-500)",
    t: "Оплата сессий не отмечена",
    s: "1 сессия",
    a: "Проверить"
  }];
  return /*#__PURE__*/React.createElement(Card, null, /*#__PURE__*/React.createElement(CardHeader, {
    title: "\u0422\u0440\u0435\u0431\u0443\u0435\u0442 \u0432\u043D\u0438\u043C\u0430\u043D\u0438\u044F",
    icon: /*#__PURE__*/React.createElement(Icon, {
      name: "quote",
      size: 16,
      style: {
        color: "var(--orange-500)"
      }
    })
  }), /*#__PURE__*/React.createElement(CardBody, {
    style: {
      padding: 12,
      display: "flex",
      flexDirection: "column",
      gap: 6
    }
  }, items.map((it, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    style: {
      display: "flex",
      alignItems: "center",
      gap: 10,
      padding: 10,
      borderRadius: 12,
      background: it.tint
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: 28,
      height: 28,
      borderRadius: 9,
      background: "rgba(255,255,255,.6)",
      color: it.ic,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      flexShrink: 0
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: it.icon,
    size: 15
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      fontWeight: 700
    }
  }, it.t), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: "var(--muted-foreground)"
    }
  }, it.s)), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 11,
      fontWeight: 700,
      color: "var(--forest-700)",
      display: "flex",
      alignItems: "center",
      gap: 2
    }
  }, it.a, " \u203A")))));
}
function WeekStats() {
  const spark = WEEK.map(w => w.c);
  const max = Math.max(...spark);
  return /*#__PURE__*/React.createElement(Card, null, /*#__PURE__*/React.createElement(CardHeader, {
    title: "\u0421\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043A\u0430 \u043D\u0435\u0434\u0435\u043B\u0438",
    icon: /*#__PURE__*/React.createElement(Icon, {
      name: "dynamics",
      size: 16,
      style: {
        color: "var(--forest-600)"
      }
    }),
    action: /*#__PURE__*/React.createElement(Badge, {
      variant: "new"
    }, "+12%")
  }), /*#__PURE__*/React.createElement(CardBody, {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 20
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "text-kpi-number",
    style: {
      color: "var(--forest-800)"
    }
  }, "17"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: "var(--muted-foreground)",
      fontWeight: 600
    }
  }, "\u0441\u0435\u0441\u0441\u0438\u0439")), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "text-kpi-number",
    style: {
      color: "var(--forest-800)"
    }
  }, "9"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: "var(--muted-foreground)",
      fontWeight: 600
    }
  }, "\u043A\u043B\u0438\u0435\u043D\u0442\u043E\u0432")), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "text-kpi-number",
    style: {
      color: "var(--forest-800)"
    }
  }, "68%"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: "var(--muted-foreground)",
      fontWeight: 600
    }
  }, "\u0437\u0430\u0433\u0440\u0443\u0437\u043A\u0430"))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "flex-end",
      gap: 6,
      height: 54
    }
  }, spark.map((v, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    style: {
      flex: 1,
      height: `${Math.max(v / max * 100, 6)}%`,
      borderRadius: 5,
      background: WEEK[i].today ? "var(--gold-500)" : "var(--sage-200)"
    }
  })))));
}
function DiaryApp() {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      minHeight: "100vh",
      background: "var(--background)",
      fontFamily: "var(--font-sans)"
    }
  }, /*#__PURE__*/React.createElement(Sidebar, null), /*#__PURE__*/React.createElement("main", {
    style: {
      flex: 1,
      padding: "28px 32px",
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "flex-start",
      justifyContent: "space-between",
      gap: 16,
      marginBottom: 22
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("h1", {
    style: {
      margin: 0,
      fontSize: 34,
      fontWeight: 700,
      letterSpacing: "-0.02em"
    }
  }, "\u0414\u043E\u0431\u0440\u044B\u0439 \u0434\u0435\u043D\u044C, \u041C\u0430\u0440\u0438\u044F \uD83D\uDC4B"), /*#__PURE__*/React.createElement("p", {
    style: {
      margin: "4px 0 0",
      fontSize: 13,
      color: "var(--muted-foreground)",
      fontWeight: 500,
      textTransform: "capitalize"
    }
  }, "\u0447\u0435\u0442\u0432\u0435\u0440\u0433, 15 \u043C\u0430\u044F 2026"), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 10
    }
  }, /*#__PURE__*/React.createElement(Button, {
    variant: "ghost",
    size: "sm",
    leadingIcon: /*#__PURE__*/React.createElement(Icon, {
      name: "share",
      size: 15
    }),
    style: {
      background: "var(--sage-100)",
      color: "var(--forest-700)"
    }
  }, "\u041E\u0442\u043F\u0440\u0430\u0432\u0438\u0442\u044C \u0441\u0441\u044B\u043B\u043A\u0443 \u043A\u043B\u0438\u0435\u043D\u0442\u0443"))), /*#__PURE__*/React.createElement(WeekStrip, null)), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "2fr 1fr",
      gap: 20,
      alignItems: "start"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 20
    }
  }, /*#__PURE__*/React.createElement(NextSession, null), /*#__PURE__*/React.createElement(Schedule, null)), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 20
    }
  }, /*#__PURE__*/React.createElement(Attention, null), /*#__PURE__*/React.createElement(WeekStats, null)))));
}
window.DiaryApp = DiaryApp;
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/diary/DiaryApp.jsx", error: String((e && e.message) || e) }); }

// ui_kits/landing/LandingApp.jsx
try { (() => {
/* СИМПАС — Marketing landing recreation (cmpas.ru home). Exports window.LandingApp. */
const {
  Button,
  Card,
  Icon,
  Badge
} = window.DesignSystem_66188c;
const MAX = 1240;
const NAV = ["Как работает", "Возможности", "Для психолога", "Безопасность", "Тариф"];
function Header() {
  return /*#__PURE__*/React.createElement("header", {
    style: {
      position: "sticky",
      top: 0,
      zIndex: 50,
      background: "rgba(247,248,244,.82)",
      backdropFilter: "blur(18px)",
      borderBottom: "1px solid color-mix(in srgb, var(--border) 70%, transparent)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      maxWidth: MAX,
      margin: "0 auto",
      padding: "0 32px",
      height: 76,
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("img", {
    src: "../../assets/logo-mark.png",
    alt: "",
    style: {
      width: 32,
      height: 32,
      borderRadius: 9
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 17,
      fontWeight: 800,
      letterSpacing: "0.12em",
      color: "var(--forest-800)"
    }
  }, "\u0421\u0418\u041C\u041F\u0410\u0421")), /*#__PURE__*/React.createElement("nav", {
    style: {
      display: "flex",
      gap: 4
    }
  }, NAV.map(l => /*#__PURE__*/React.createElement("a", {
    key: l,
    href: "#",
    style: {
      padding: "8px 14px",
      fontSize: 14,
      fontWeight: 500,
      color: "var(--muted-foreground)",
      borderRadius: 10,
      textDecoration: "none"
    }
  }, l))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 10
    }
  }, /*#__PURE__*/React.createElement(Button, {
    variant: "secondary",
    size: "sm"
  }, "\u0412\u043E\u0439\u0442\u0438"), /*#__PURE__*/React.createElement(Button, {
    variant: "primary",
    size: "sm"
  }, "\u041F\u043E\u043F\u0440\u043E\u0431\u043E\u0432\u0430\u0442\u044C \u0431\u0435\u0441\u043F\u043B\u0430\u0442\u043D\u043E"))));
}
function Hero() {
  return /*#__PURE__*/React.createElement("section", {
    style: {
      background: "linear-gradient(to bottom, color-mix(in srgb, var(--sage-50) 80%, transparent), var(--background))",
      padding: "72px 0 64px"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      maxWidth: MAX,
      margin: "0 auto",
      padding: "0 32px",
      display: "grid",
      gridTemplateColumns: "1fr 1fr",
      gap: 48,
      alignItems: "center"
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "inline-flex",
      alignItems: "center",
      gap: 8,
      padding: "6px 14px",
      background: "var(--sage-100)",
      borderRadius: 999,
      marginBottom: 24
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      width: 6,
      height: 6,
      borderRadius: "50%",
      background: "var(--forest-800)"
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 12,
      fontWeight: 600,
      color: "var(--forest-800)"
    }
  }, "\u0423\u043C\u043D\u044B\u0439 \u043A\u0430\u0431\u0438\u043D\u0435\u0442 \u043F\u0441\u0438\u0445\u043E\u043B\u043E\u0433\u0430")), /*#__PURE__*/React.createElement("h1", {
    style: {
      fontSize: 52,
      fontWeight: 700,
      lineHeight: 1.1,
      letterSpacing: "-0.02em",
      margin: "0 0 20px"
    }
  }, "\u041F\u043E\u0440\u044F\u0434\u043E\u043A \u0432 \u043F\u0440\u0430\u043A\u0442\u0438\u043A\u0435.", /*#__PURE__*/React.createElement("br", null), "\u0412\u0440\u0435\u043C\u044F \u043D\u0430 \u0433\u043B\u0430\u0432\u043D\u043E\u0435.", /*#__PURE__*/React.createElement("br", null), /*#__PURE__*/React.createElement("span", {
    style: {
      color: "var(--forest-800)"
    }
  }, "\u0420\u043E\u0441\u0442 \u0431\u0435\u0437 \u0445\u0430\u043E\u0441\u0430.")), /*#__PURE__*/React.createElement("p", {
    style: {
      fontSize: 17,
      lineHeight: 1.6,
      color: "var(--muted-foreground)",
      maxWidth: 440,
      margin: "0 0 32px"
    }
  }, "\u041A\u043B\u0438\u0435\u043D\u0442\u044B \u0441\u0430\u043C\u0438 \u0437\u0430\u043F\u0438\u0441\u044B\u0432\u0430\u044E\u0442\u0441\u044F \u043D\u0430 \u0441\u0435\u0441\u0441\u0438\u0438, \u0430 \u0432\u044B \u0432\u0438\u0434\u0438\u0442\u0435 \u0434\u0435\u043D\u044C, \u0440\u0430\u0441\u043F\u0438\u0441\u0430\u043D\u0438\u0435, \u043A\u0430\u0440\u0442\u043E\u0447\u043A\u0438 \u043A\u043B\u0438\u0435\u043D\u0442\u043E\u0432 \u0438 \u0437\u0430\u043C\u0435\u0442\u043A\u0438 \u0432 \u043E\u0434\u043D\u043E\u043C \u0441\u043F\u043E\u043A\u043E\u0439\u043D\u043E\u043C \u0440\u0430\u0431\u043E\u0447\u0435\u043C \u043F\u0440\u043E\u0441\u0442\u0440\u0430\u043D\u0441\u0442\u0432\u0435."), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 12,
      marginBottom: 22
    }
  }, /*#__PURE__*/React.createElement(Button, {
    variant: "primary",
    size: "lg"
  }, "\u041F\u043E\u043F\u0440\u043E\u0431\u043E\u0432\u0430\u0442\u044C \u0431\u0435\u0441\u043F\u043B\u0430\u0442\u043D\u043E"), /*#__PURE__*/React.createElement(Button, {
    variant: "secondary",
    size: "lg"
  }, "\u0417\u0430\u043F\u0438\u0441\u044C \u0433\u043B\u0430\u0437\u0430\u043C\u0438 \u043A\u043B\u0438\u0435\u043D\u0442\u0430")), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 8,
      flexWrap: "wrap"
    }
  }, ["30 дней бесплатно", "Telegram и MAX", "Данные под контролем"].map(c => /*#__PURE__*/React.createElement("span", {
    key: c,
    style: {
      padding: "6px 12px",
      background: "var(--card)",
      border: "1px solid var(--border)",
      borderRadius: 999,
      fontSize: 12,
      fontWeight: 500,
      color: "var(--muted-foreground)"
    }
  }, c)))), /*#__PURE__*/React.createElement("div", {
    style: {
      position: "relative"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      borderRadius: 20,
      overflow: "hidden",
      boxShadow: "var(--shadow-floating)",
      border: "1px solid var(--border)",
      aspectRatio: "4/3",
      background: "var(--card)"
    }
  }, /*#__PURE__*/React.createElement("img", {
    src: "../../public/images/main_screen_photo_v2.jpg",
    alt: "",
    style: {
      width: "100%",
      height: "100%",
      objectFit: "cover"
    }
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      position: "absolute",
      bottom: -18,
      left: -18,
      background: "var(--card)",
      borderRadius: 16,
      border: "1px solid var(--border)",
      boxShadow: "var(--shadow-card)",
      padding: "12px 16px",
      display: "flex",
      alignItems: "center",
      gap: 12
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: 32,
      height: 32,
      borderRadius: "50%",
      background: "var(--sage-100)",
      color: "var(--forest-700)",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      fontWeight: 700,
      fontSize: 11
    }
  }, "\u041C\u0421"), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      fontWeight: 700
    }
  }, "\u041C\u0430\u0440\u0438\u044F \u0437\u0430\u043F\u0438\u0441\u0430\u043B\u0430\u0441\u044C"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: "var(--muted-foreground)"
    }
  }, "15 \u043C\u0430\u044F \u0432 19:00")), /*#__PURE__*/React.createElement("span", {
    style: {
      width: 8,
      height: 8,
      borderRadius: "50%",
      background: "var(--forest-800)"
    }
  })))));
}
const FEATURES = [{
  icon: "anamnesis",
  t: "Запись клиентов",
  d: "Клиент сам выбирает удобное время, формат и подтверждает запись.",
  bg: "var(--blue-soft)",
  c: "var(--blue-500)"
}, {
  icon: "sync",
  t: "Расписание",
  d: "Рабочие дни, окна, перерывы и синхронизация с календарём.",
  bg: "var(--amber-soft)",
  c: "var(--gold-500)"
}, {
  icon: "for_client",
  t: "Клиенты",
  d: "Карточки клиентов, история сессий и важные детали под рукой.",
  bg: "var(--sage-100)",
  c: "var(--forest-800)"
}, {
  icon: "format",
  t: "Заметки",
  d: "Структурные заметки после сессии, приватные записи и резюме.",
  bg: "var(--violet-soft)",
  c: "var(--violet-500)"
}, {
  icon: "meeting",
  t: "Бот-ассистент",
  d: "Telegram и MAX напоминают о новых записях и сессиях.",
  bg: "var(--blue-soft)",
  c: "var(--blue-500)"
}, {
  icon: "save",
  t: "Документы и согласия",
  d: "Шаблоны документов и электронные согласия в одном месте.",
  bg: "var(--red-soft)",
  c: "var(--red-500)"
}];
function Features() {
  return /*#__PURE__*/React.createElement("section", {
    style: {
      padding: "60px 0",
      background: "var(--background)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      maxWidth: MAX,
      margin: "0 auto",
      padding: "0 32px"
    }
  }, /*#__PURE__*/React.createElement("h2", {
    style: {
      fontSize: 36,
      fontWeight: 700,
      letterSpacing: "-0.015em",
      margin: "0 0 8px"
    }
  }, "\u0412\u043E\u0437\u043C\u043E\u0436\u043D\u043E\u0441\u0442\u0438 \u043A\u0430\u0431\u0438\u043D\u0435\u0442\u0430"), /*#__PURE__*/React.createElement("p", {
    style: {
      fontSize: 16,
      color: "var(--muted-foreground)",
      margin: "0 0 40px"
    }
  }, "\u0412\u0441\u0451, \u0447\u0442\u043E \u043D\u0443\u0436\u043D\u043E \u0434\u043B\u044F \u0441\u043F\u043E\u043A\u043E\u0439\u043D\u043E\u0439 \u0447\u0430\u0441\u0442\u043D\u043E\u0439 \u043F\u0440\u0430\u043A\u0442\u0438\u043A\u0438."), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "repeat(3,1fr)",
      gap: 16
    }
  }, FEATURES.map(f => /*#__PURE__*/React.createElement(Card, {
    key: f.t,
    interactive: true,
    padding: 24
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: 40,
      height: 40,
      borderRadius: 12,
      background: f.bg,
      color: f.c,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      marginBottom: 14
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: f.icon,
    size: 20
  })), /*#__PURE__*/React.createElement("h3", {
    style: {
      fontSize: 15,
      fontWeight: 700,
      margin: "0 0 6px"
    }
  }, f.t), /*#__PURE__*/React.createElement("p", {
    style: {
      fontSize: 13,
      lineHeight: 1.55,
      color: "var(--muted-foreground)",
      margin: 0
    }
  }, f.d))))));
}
const POINTS = [{
  icon: "quote",
  t: "Меньше переписки",
  d: "Клиент записывается сам, бот берёт напоминания на себя."
}, {
  icon: "resources",
  t: "Меньше забытых деталей",
  d: "История, заметки и документы рядом с карточкой клиента."
}, {
  icon: "hypothesis",
  t: "Больше ясности в практике",
  d: "Видно день, клиентов, сессии и следующие шаги."
}];
function Positioning() {
  return /*#__PURE__*/React.createElement("section", {
    style: {
      padding: "84px 0",
      background: "radial-gradient(circle at 80% 20%, rgba(204,158,80,.16), transparent 34%), linear-gradient(135deg,#143D2F 0%,#1D4735 55%,#285B46 100%)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      maxWidth: MAX,
      margin: "0 auto",
      padding: "0 32px",
      textAlign: "center"
    }
  }, /*#__PURE__*/React.createElement("h2", {
    style: {
      fontSize: 40,
      fontWeight: 700,
      letterSpacing: "-0.015em",
      color: "#fff",
      margin: "0 0 16px"
    }
  }, "\u041D\u0435 CRM. \u041D\u0435 \u0442\u0430\u0431\u043B\u0438\u0446\u0430.", /*#__PURE__*/React.createElement("br", null), /*#__PURE__*/React.createElement("span", {
    style: {
      color: "rgba(255,255,255,.8)"
    }
  }, "\u0420\u0430\u0431\u043E\u0447\u0435\u0435 \u043F\u0440\u043E\u0441\u0442\u0440\u0430\u043D\u0441\u0442\u0432\u043E")), /*#__PURE__*/React.createElement("p", {
    style: {
      fontSize: 16,
      color: "rgba(255,255,255,.6)",
      maxWidth: 520,
      margin: "0 auto 48px",
      lineHeight: 1.6
    }
  }, "\u0421\u0418\u041C\u041F\u0410\u0421 \u0443\u0431\u0438\u0440\u0430\u0435\u0442 \u0440\u0443\u0442\u0438\u043D\u0443 \u0432\u043E\u043A\u0440\u0443\u0433 \u043F\u0440\u0430\u043A\u0442\u0438\u043A\u0438, \u0447\u0442\u043E\u0431\u044B \u0443 \u043F\u0441\u0438\u0445\u043E\u043B\u043E\u0433\u0430 \u043E\u0441\u0442\u0430\u0432\u0430\u043B\u043E\u0441\u044C \u0431\u043E\u043B\u044C\u0448\u0435 \u0432\u043D\u0438\u043C\u0430\u043D\u0438\u044F \u043D\u0430 \u043A\u043E\u043D\u0442\u0430\u043A\u0442 \u0441 \u043A\u043B\u0438\u0435\u043D\u0442\u043E\u043C."), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "repeat(3,1fr)",
      gap: 24
    }
  }, POINTS.map(p => /*#__PURE__*/React.createElement("div", {
    key: p.t,
    style: {
      background: "rgba(255,255,255,.08)",
      backdropFilter: "blur(4px)",
      borderRadius: 24,
      border: "1px solid rgba(255,255,255,.1)",
      padding: 28,
      textAlign: "left"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: 40,
      height: 40,
      borderRadius: 12,
      background: "rgba(255,255,255,.1)",
      color: "rgba(255,255,255,.85)",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      marginBottom: 16
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: p.icon,
    size: 20
  })), /*#__PURE__*/React.createElement("h3", {
    style: {
      fontSize: 16,
      fontWeight: 700,
      color: "#fff",
      margin: "0 0 8px"
    }
  }, p.t), /*#__PURE__*/React.createElement("p", {
    style: {
      fontSize: 14,
      lineHeight: 1.55,
      color: "rgba(255,255,255,.6)",
      margin: 0
    }
  }, p.d))))));
}
function Pricing() {
  const incl = ["Все возможности СИМПАСА", "Бот и напоминания клиентам", "Расписание и запись", "Заметки и карточки клиентов", "Документы и согласия", "Поддержка и обновления"];
  return /*#__PURE__*/React.createElement("section", {
    style: {
      padding: "72px 0",
      background: "var(--background)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      maxWidth: 480,
      margin: "0 auto",
      padding: "0 32px",
      textAlign: "center"
    }
  }, /*#__PURE__*/React.createElement("h2", {
    style: {
      fontSize: 36,
      fontWeight: 700,
      letterSpacing: "-0.015em",
      margin: "0 0 8px"
    }
  }, "\u041F\u0440\u043E\u0441\u0442\u043E \u0438 \u043F\u0440\u043E\u0437\u0440\u0430\u0447\u043D\u043E"), /*#__PURE__*/React.createElement("p", {
    style: {
      fontSize: 16,
      color: "var(--muted-foreground)",
      margin: "0 0 40px",
      lineHeight: 1.6
    }
  }, "30 \u0434\u043D\u0435\u0439 \u0431\u0435\u0441\u043F\u043B\u0430\u0442\u043D\u043E. \u042D\u0442\u043E\u0433\u043E \u0434\u043E\u0441\u0442\u0430\u0442\u043E\u0447\u043D\u043E, \u0447\u0442\u043E\u0431\u044B \u043D\u0430\u0441\u0442\u0440\u043E\u0438\u0442\u044C \u043A\u0430\u0431\u0438\u043D\u0435\u0442 \u0438 \u043F\u0440\u043E\u0432\u0435\u0441\u0442\u0438 \u043F\u0435\u0440\u0432\u044B\u0435 \u0437\u0430\u043F\u0438\u0441\u0438."), /*#__PURE__*/React.createElement(Card, {
    padding: 36,
    style: {
      textAlign: "left"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      textAlign: "center",
      marginBottom: 24
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "baseline",
      justifyContent: "center",
      gap: 4
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 56,
      fontWeight: 700,
      letterSpacing: "-0.02em"
    }
  }, "990"), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 20,
      fontWeight: 600,
      color: "var(--muted-foreground)"
    }
  }, "\u20BD")), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 14,
      color: "var(--muted-foreground)",
      fontWeight: 500
    }
  }, "\u0432 \u043C\u0435\u0441\u044F\u0446, \u043F\u043E\u0441\u043B\u0435 \u043F\u0440\u043E\u0431\u043D\u043E\u0433\u043E \u043F\u0435\u0440\u0438\u043E\u0434\u0430")), /*#__PURE__*/React.createElement("div", {
    style: {
      height: 1,
      background: "var(--border)",
      margin: "0 0 22px"
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 14,
      marginBottom: 28
    }
  }, incl.map(it => /*#__PURE__*/React.createElement("div", {
    key: it,
    style: {
      display: "flex",
      alignItems: "center",
      gap: 12
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      width: 20,
      height: 20,
      borderRadius: "50%",
      background: "var(--sage-100)",
      color: "var(--forest-800)",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      flexShrink: 0
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "next_step",
    size: 13,
    strokeWidth: 2.4
  })), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 14,
      fontWeight: 500
    }
  }, it)))), /*#__PURE__*/React.createElement(Button, {
    variant: "primary",
    block: true,
    size: "lg"
  }, "\u041F\u043E\u043F\u0440\u043E\u0431\u043E\u0432\u0430\u0442\u044C \u0431\u0435\u0441\u043F\u043B\u0430\u0442\u043D\u043E"), /*#__PURE__*/React.createElement("p", {
    style: {
      textAlign: "center",
      fontSize: 12,
      color: "var(--muted-foreground)",
      marginTop: 16
    }
  }, "\u0411\u0435\u0437 \u043E\u043F\u043B\u0430\u0442\u044B \u043D\u0430 \u0441\u0442\u0430\u0440\u0442\u0435 \xB7 \u043E\u0442\u043C\u0435\u043D\u0430 \u0432 \u043B\u044E\u0431\u043E\u0439 \u043C\u043E\u043C\u0435\u043D\u0442"))));
}
function Footer() {
  return /*#__PURE__*/React.createElement("footer", {
    style: {
      background: "var(--forest-800)",
      color: "#fff",
      padding: "40px 0"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      maxWidth: MAX,
      margin: "0 auto",
      padding: "0 32px",
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("img", {
    src: "../../assets/logo-mark.png",
    alt: "",
    style: {
      width: 28,
      height: 28,
      borderRadius: 8
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 15,
      fontWeight: 800,
      letterSpacing: "0.12em"
    }
  }, "\u0421\u0418\u041C\u041F\u0410\u0421")), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 12,
      color: "rgba(255,255,255,.4)"
    }
  }, "\xA9 2026 \u0421\u0418\u041C\u041F\u0410\u0421. \u0423\u043C\u043D\u044B\u0439 \u043A\u0430\u0431\u0438\u043D\u0435\u0442 \u043F\u0441\u0438\u0445\u043E\u043B\u043E\u0433\u0430.")));
}
function LandingApp() {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      fontFamily: "var(--font-sans)",
      background: "var(--background)"
    }
  }, /*#__PURE__*/React.createElement(Header, null), /*#__PURE__*/React.createElement(Hero, null), /*#__PURE__*/React.createElement(Features, null), /*#__PURE__*/React.createElement(Positioning, null), /*#__PURE__*/React.createElement(Pricing, null), /*#__PURE__*/React.createElement(Footer, null));
}
window.LandingApp = LandingApp;
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/landing/LandingApp.jsx", error: String((e && e.message) || e) }); }

__ds_ns.SERVICES = __ds_scope.SERVICES;

__ds_ns.ServiceMark = __ds_scope.ServiceMark;

__ds_ns.Badge = __ds_scope.Badge;

__ds_ns.Button = __ds_scope.Button;

__ds_ns.Card = __ds_scope.Card;

__ds_ns.CardHeader = __ds_scope.CardHeader;

__ds_ns.CardBody = __ds_scope.CardBody;

__ds_ns.Input = __ds_scope.Input;

__ds_ns.Separator = __ds_scope.Separator;

__ds_ns.ICON_NAMES = __ds_scope.ICON_NAMES;

__ds_ns.Icon = __ds_scope.Icon;

__ds_ns.SegmentedControl = __ds_scope.SegmentedControl;

})();
