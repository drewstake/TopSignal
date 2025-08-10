export default function Button({ variant = "primary", className = "", disabled, children, ...props }) {
  const base =
    "inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2 font-medium transition-all duration-300 disabled:opacity-60 disabled:cursor-not-allowed";
  const styles = {
    primary:
      "bg-gradient-to-br from-indigo-500 to-fuchsia-600 text-white shadow-lg hover:shadow-xl hover:brightness-110",
    ghost: "bg-white/5 text-zinc-200 hover:bg-white/10",
    danger:
      "bg-gradient-to-br from-rose-500 to-orange-600 text-white shadow-lg hover:shadow-xl hover:brightness-110",
  };
  return (
    <button className={`${base} ${styles[variant]} ${className}`} disabled={disabled} {...props}>
      {children}
    </button>
  );
}
