import type {
  AccessTokenResponse,
  AvailableRepository,
  IndexingStatus,
  Installation,
  QuestionResponse,
  Repository,
  RepositoryJobResponse,
  User,
} from "./contracts";

const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL as string | undefined;
const API_BASE_URL = (configuredBaseUrl ?? "http://127.0.0.1:8000/api/v1").replace(/\/$/, "");

export class ApiError extends Error {
  public readonly status: number;
  public readonly code: string | undefined;
  public readonly requestId: string | undefined;

  public constructor(status: number, message: string, code?: string, requestId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

export class ApiProtocolError extends Error {
  public constructor() {
    super("The API returned an unexpected response. Please try again.");
    this.name = "ApiProtocolError";
  }
}

interface RequestOptions extends Omit<RequestInit, "body" | "headers"> {
  accessToken?: string | null;
  body?: unknown;
  headers?: HeadersInit;
}

function isErrorPayload(value: unknown): value is {
  error?: { code?: string; message?: string; request_id?: string };
} {
  return typeof value === "object" && value !== null;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { accessToken, body, headers, ...init } = options;
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      Accept: "application/json",
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 204) {
    return undefined as T;
  }
  const contentType = response.headers.get("content-type") ?? "";
  let data: unknown = null;
  if (contentType.includes("application/json")) {
    try {
      data = await response.json();
    } catch {
      if (response.ok) throw new ApiProtocolError();
    }
  }
  if (!response.ok) {
    const payload = isErrorPayload(data) ? data.error : undefined;
    throw new ApiError(
      response.status,
      payload?.message ?? "The request could not be completed.",
      payload?.code,
      payload?.request_id,
    );
  }
  if (!contentType.includes("application/json") || data === null) throw new ApiProtocolError();
  return data as T;
}

export const api = {
  apiBaseUrl: API_BASE_URL,
  startGitHubAuthorization(): void {
    window.location.assign(`${API_BASE_URL}/auth/github/start`);
  },
  refresh(signal?: AbortSignal): Promise<AccessTokenResponse> {
    return request<AccessTokenResponse>("/auth/refresh", {
      method: "POST",
      signal,
    });
  },
  logout(accessToken: string | null): Promise<void> {
    return request<void>("/auth/logout", {
      method: "POST",
      accessToken,
    });
  },
  getCurrentUser(accessToken: string, signal?: AbortSignal): Promise<User> {
    return request<User>("/auth/me", { accessToken, signal });
  },
  listInstallations(accessToken: string, signal?: AbortSignal): Promise<Installation[]> {
    return request<Installation[]>("/installations", { accessToken, signal });
  },
  listInstallationRepositories(
    accessToken: string,
    installationId: string,
    signal?: AbortSignal,
  ): Promise<AvailableRepository[]> {
    return request<AvailableRepository[]>(
      `/installations/${encodeURIComponent(installationId)}/repositories`,
      {
        accessToken,
        signal,
      },
    );
  },
  listRepositories(accessToken: string, signal?: AbortSignal): Promise<Repository[]> {
    return request<Repository[]>("/repositories", { accessToken, signal });
  },
  connectRepository(
    accessToken: string,
    installationId: string,
    githubRepositoryId: number,
  ): Promise<RepositoryJobResponse> {
    return request<RepositoryJobResponse>("/repositories", {
      method: "POST",
      accessToken,
      body: { installation_id: installationId, github_repository_id: githubRepositoryId },
    });
  },
  getRepository(
    accessToken: string,
    repositoryId: string,
    signal?: AbortSignal,
  ): Promise<Repository> {
    return request<Repository>(`/repositories/${encodeURIComponent(repositoryId)}`, {
      accessToken,
      signal,
    });
  },
  getRepositoryStatus(
    accessToken: string,
    repositoryId: string,
    signal?: AbortSignal,
  ): Promise<IndexingStatus> {
    return request<IndexingStatus>(`/repositories/${encodeURIComponent(repositoryId)}/status`, {
      accessToken,
      signal,
    });
  },
  reindexRepository(accessToken: string, repositoryId: string): Promise<RepositoryJobResponse> {
    return request<RepositoryJobResponse>(
      `/repositories/${encodeURIComponent(repositoryId)}/reindex`,
      {
        method: "POST",
        accessToken,
      },
    );
  },
  askQuestion(
    accessToken: string,
    repositoryId: string,
    question: string,
    signal?: AbortSignal,
  ): Promise<QuestionResponse> {
    return request<QuestionResponse>(
      `/repositories/${encodeURIComponent(repositoryId)}/questions`,
      {
        method: "POST",
        accessToken,
        signal,
        body: { question },
      },
    );
  },
};
