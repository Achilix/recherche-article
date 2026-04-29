import type { Metadata } from "next";
import { Geist_Mono } from "next/font/google";
import "./globals.css";

// We load system/Google fonts via CSS @import in globals.css
// Only keeping Geist Mono here for code-like fallback uses if needed
const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Lexis — Legal AI Search",
  description:
    "Semantic search across your embedded legal corpus. Ask in French or English.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="fr" className={`${geistMono.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
