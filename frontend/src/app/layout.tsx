import type { Metadata } from "next";
import "./globals.css";
import { AppProvider } from "@/context/AppContext";
import { Navbar } from "@/components/layout/Navbar";
import { Sidebar } from "@/components/layout/Sidebar";

export const metadata: Metadata = {
  title: "SAIL | Enterprise Bulk Vessel Chartering & Logistics Management Dashboard",
  description:
    "Enterprise Overseas Bulk Vessel Chartering & Raw Material Logistics Management Dashboard for Steel Authority of India Limited (Ministry of Steel)",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="bg-sail-darkest text-slate-100 min-h-screen flex flex-col antialiased selection:bg-sail-cyan selection:text-black">
        <AppProvider>
          <Navbar />
          <div className="flex flex-1">
            <Sidebar />
            <main className="flex-1 p-6 overflow-y-auto max-w-7xl mx-auto w-full">
              {children}
            </main>
          </div>
        </AppProvider>
      </body>
    </html>
  );
}
