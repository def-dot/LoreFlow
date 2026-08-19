class Node:

    def set_next(self, node: Node) -> None:
        self.next = node


node1 = Node()
node2 = Node()
node1.set_next(node2)
