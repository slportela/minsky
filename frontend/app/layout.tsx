import Link from "next/link";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata = { title: "minsky: dispute intake" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="es">
      <body>
        <header className="topbar">
          <Link href="/" className="brand">
            minsky
          </Link>
          <nav aria-label="Principal">
            <Link href="/chat">Chat</Link>
            <Link href="/console">Consola</Link>
          </nav>
        </header>
        {children}
      </body>
    </html>
  );
}
