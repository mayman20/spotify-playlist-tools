import { useEffect, useState } from "react";
import { fetchMe, getSessionToken } from "@/lib/api";

type CurrentUser = {
  spotify_user_id: string;
  display_name: string;
};

export function useCurrentUser() {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const token = getSessionToken();
    if (!token) {
      setUser(null);
      return;
    }
    setLoading(true);
    fetchMe()
      .then((data) => setUser(data))
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  return { user, loading };
}
