import { Link } from "react-router-dom";

export default function NotFoundPage() {
  return (
    <div className="rounded-2xl border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-900/40">
      <div className="text-xl font-semibold text-zinc-900 dark:text-zinc-100">Page not found</div>
      <div className="mt-2 text-sm text-zinc-600 dark:text-zinc-400">The route does not exist.</div>
      <Link className="mt-4 inline-block rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-700 shadow-sm transition hover:border-zinc-400 dark:border-zinc-800 dark:bg-zinc-950/40 dark:text-zinc-200" to="/">
        Go home
      </Link>
    </div>
  );
}
