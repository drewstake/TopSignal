export default function Card({ className = "", children }) {
  return (
    <div
      className={
        "rounded-2xl bg-white/5 backdrop-blur-xl border border-white/10 shadow-2xl shadow-black/40 " +
        "hover:border-white/20 transition-all duration-300 " + className
      }
    >{children}</div>
  );
}
