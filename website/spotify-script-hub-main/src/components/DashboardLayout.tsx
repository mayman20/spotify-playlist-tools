import { SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { AppSidebar } from "@/components/AppSidebar";
import { Outlet } from "react-router-dom";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { useCurrentUser } from "@/hooks/use-current-user";

export function DashboardLayout() {
  const { user, loading } = useCurrentUser();
  const name = loading ? "Loading..." : user?.display_name || "Not Connected";
  const fallback = (user?.display_name || "NC").slice(0, 2).toUpperCase();

  return (
    <SidebarProvider>
      <div className="min-h-screen flex w-full">
        <AppSidebar />
        <div className="flex-1 flex flex-col">
          <header className="h-14 flex items-center justify-between border-b px-3 sm:px-4">
            <SidebarTrigger />
            <div className="flex items-center gap-3">
              <span className="hidden sm:inline text-sm text-muted-foreground">{name}</span>
              <Avatar className="h-8 w-8">
                <AvatarImage src="" />
                <AvatarFallback>{fallback}</AvatarFallback>
              </Avatar>
            </div>
          </header>
          <main className="flex-1 p-3 sm:p-4 lg:p-6 overflow-auto">
            <Outlet />
          </main>
        </div>
      </div>
    </SidebarProvider>
  );
}
