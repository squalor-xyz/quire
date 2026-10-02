# Diagram examples

## Example 1

```mermaid
flowchart TD
  A[Start] --> B[Finish]
```

## Example 2

```mermaid
flowchart LR
  A[Request] -->|accepted| B[Response]
```

## Example 3

```mermaid
flowchart TD
  subgraph Group One
    A[First] --> B[Second]
  end
  B --> C[Outside]
```

## Example 4

```mermaid
flowchart LR
  A[Producer] -.->|optional| B[Consumer]
```

## Example 5

```mermaid
flowchart TD
  A{Proceed?} -->|Yes| B[Continue]
  A -->|No| C[Stop]
```

## Example 6

```mermaid
flowchart LR
  A["First line<br/>Second line"] --> B["Main label<br/><small>Small detail</small>"]
```

## Example 7

```mermaid
flowchart TD
  subgraph Outer
    subgraph Inner
      A[One] --> B[Two]
    end
    B -.-> C[Three]
  end
```

## Example 8

```mermaid
flowchart LR
  A["Long label that stays inside its node shape"] -->|"Two lines<br/><small>Extra detail</small>"| B[Result]
```

## Example 9

```mermaid
flowchart TD
  A[Collect] --> B[Validate]
  B --> C[Transform]
  C --> D[Review]
  D --> E[Approve]
  E --> F[Publish]
  F --> G[Archive]
```

## Example 10

~~~{#relationships .mermaid}
erDiagram
  PERSON ||--o{ DOCUMENT : authors
  DOCUMENT ||--|{ SECTION : contains
  PERSON {
    int id PK
    string name
  }
  DOCUMENT {
    int id PK
    string title
  }
  SECTION {
    int number
    string heading
  }
~~~

## Example 11

```mermaid
sequenceDiagram
  Reader->>Writer: Request
  Writer-->>Reader: Reply
```
