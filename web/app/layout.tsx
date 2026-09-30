import "./globals.css";
import Link from "next/link";

export const metadata = { title: "fairfare", description: "Verified, cost-transparent family trip planning" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <nav>
          <strong>fairfare</strong>
          <Link href="/">Plan a trip</Link>
          <Link href="/audit">Audit a quote</Link>
          <Link href="/bookings">Bookings</Link>
          <Link href="/runs">Runs</Link>
        </nav>
        <main>{children}</main>
      </body>
    </html>
  );
}
